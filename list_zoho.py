"""
List what Zoho Books holds, so you can find the exact names and IDs.

ledger_id       comes from the chart of accounts. add_missing_vendors.py
                resolves it from a ledger NAME, and the name has to match Zoho
                exactly - so look it up here rather than guessing.

zoho_contact_id comes from the vendor contact. You do not normally need to read
                it yourself: the script finds it by GSTIN, falling back to name.
                This is for checking whether a vendor exists in Zoho at all, and
                under what spelling.

    python list_zoho.py accounts
    python list_zoho.py accounts --search software
    python list_zoho.py vendors --search sumo
    python list_zoho.py vendors --missing        # only those not in our database
"""
import argparse
import asyncio
import re

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from zoho_client import zoho_books_client


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


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
    ap.add_argument("what", choices=["accounts", "vendors"])
    ap.add_argument("--search", default=None, help="filter by a word in the name")
    ap.add_argument("--missing", action="store_true",
                    help="vendors only: show just those not in invoice_db.vendors")
    args = ap.parse_args()

    if args.what == "accounts":
        accounts = await fetch_all(zoho_books_client.get_chartofaccounts)
        rows = [a for a in accounts
                if not args.search or args.search.lower() in str(a.get("account_name", "")).lower()]
        # Expense accounts first - those are the ones vendors are booked against.
        rows.sort(key=lambda a: (not str(a.get("account_type", "")).startswith("expense"),
                                 str(a.get("account_name", ""))))
        print(f"{len(rows)} of {len(accounts)} accounts\n")
        print(f"{'ACCOUNT NAME':58} {'TYPE':22} ACCOUNT ID (ledger_id)")
        for a in rows:
            print(f"{str(a.get('account_name'))[:57]:58} "
                  f"{str(a.get('account_type'))[:21]:22} {a.get('account_id')}")
        print("\nUse the ACCOUNT NAME in the master sheet's Ledger column, spelled exactly as above.")
        return

    contacts = await fetch_all(zoho_books_client.get_vendors)

    known = set()
    if args.missing:
        db = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)["invoice_db"]
        for v in await db["vendors"].find({}, {"vendor_name": 1, "gstin": 1, "_id": 0}).to_list(None):
            known.add(norm(v.get("vendor_name")))
            if v.get("gstin"):
                known.add(norm(v.get("gstin")))

    rows = []
    for c in contacts:
        name = str(c.get("contact_name") or "")
        if args.search and args.search.lower() not in name.lower():
            continue
        if args.missing and (norm(name) in known or norm(c.get("gst_no")) in known):
            continue
        rows.append(c)
    rows.sort(key=lambda c: str(c.get("contact_name") or "").lower())

    label = "in Zoho but not in our database" if args.missing else "vendor contacts"
    print(f"{len(rows)} {label} (of {len(contacts)} in Zoho)\n")
    print(f"{'CONTACT NAME':50} {'GSTIN':18} CONTACT ID (zoho_contact_id)")
    for c in rows:
        print(f"{str(c.get('contact_name'))[:49]:50} "
              f"{str(c.get('gst_no') or '-')[:17]:18} {c.get('contact_id')}")


if __name__ == "__main__":
    asyncio.run(main())
