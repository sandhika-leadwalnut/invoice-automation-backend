"""
Set the ledger on a vendor that was added without one.

add_one_vendor.py --no-ledger lets a vendor into the database before Finance has
decided which expense account they belong to. Everything works except the Zoho
push: with no ledger_id, resolve_vendor_zoho_contact leaves account_id off the
line items and Zoho books the bill wherever it likes. This closes that.

    python set_vendor_ledger.py                      # list vendors still waiting
    python set_vendor_ledger.py "R B Gaikwad" --ledger "Professional Charges"
    python set_vendor_ledger.py "R B Gaikwad" --ledger "Professional Charges" --apply
"""
import argparse
import asyncio
import difflib
import re

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from zoho_client import zoho_books_client


def norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


async def fetch_all(fetcher):
    out, page = [], 1
    while True:
        batch = await fetcher(page=page, per_page=200)
        if not batch:
            break
        out.extend(batch)
        page += 1
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendor", nargs="?", default=None, help="vendor name, or omit to list those waiting")
    ap.add_argument("--ledger", default=None, help="exact ledger name from the Zoho chart of accounts")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    vendors = client["invoice_db"]["vendors"]

    if not args.vendor:
        waiting = await vendors.find(
            {"$or": [{"ledger_pending": True}, {"ledger_id": {"$in": [None, ""]}},
                     {"ledger_id": {"$exists": False}}]}
        ).to_list(None)
        if not waiting:
            print("Every vendor has a ledger.")
            return
        print(f"{len(waiting)} vendor(s) have no ledger:\n")
        for v in waiting:
            print(f"   {v.get('vendor_name')}")
        print("\nAny invoice accepted for these posts to Zoho without an account.")
        return

    doc = await vendors.find_one(
        {"vendor_name": re.compile(f"^{re.escape(args.vendor)}$", re.IGNORECASE)})
    if not doc:
        loose = await vendors.find(
            {"vendor_name": re.compile(re.escape(args.vendor), re.IGNORECASE)}).to_list(10)
        print(f"!! No vendor matches {args.vendor!r}.")
        for v in loose:
            print(f"   did you mean: {v.get('vendor_name')}")
        return

    if not args.ledger:
        print(f"{doc.get('vendor_name')} currently has ledger: {doc.get('ledger_name') or '(none)'}")
        print("Give --ledger to set it.")
        return

    print("Reading the Zoho chart of accounts...")
    accounts = await fetch_all(zoho_books_client.get_chartofaccounts)

    wanted = norm(args.ledger)
    account = next((a for a in accounts if norm(a.get("account_name")) == wanted), None)
    if not account:
        close = difflib.get_close_matches(
            args.ledger, [a.get("account_name", "") for a in accounts], n=5, cutoff=0.5)
        print(f"!! {args.ledger!r} is not a ledger in the chart of accounts.")
        if close:
            print(f"   Did you mean: {', '.join(close)}")
        return

    print(f"\nVENDOR : {doc.get('vendor_name')}")
    print(f"   ledger_name  {doc.get('ledger_name') or '(none)'}  ->  {account['account_name']}")
    print(f"   ledger_id    {doc.get('ledger_id') or '(none)'}  ->  {account['account_id']}")

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply.")
        return

    await vendors.update_one(
        {"vendor_id": doc["vendor_id"]},
        {
            "$set": {"ledger_name": account["account_name"],
                     "ledger_id": str(account["account_id"])},
            "$unset": {"ledger_pending": ""},
        },
    )
    print("\nSet. Invoices from this vendor will now post to the right account.")
    print("Add the ledger to Vendor-Ledgers.xlsx and the master sheet too.")


if __name__ == "__main__":
    asyncio.run(main())
