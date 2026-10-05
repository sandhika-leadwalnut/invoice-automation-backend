"""
Work out a vendor's ledger from their past bills in Zoho.

A Zoho contact record does not carry a default expense account, so there is no
field to read. But every bill line does: each one is booked to an account_id.
So if a vendor has been billed before, Zoho already knows which ledger they
belong to - it just has to be read off their history rather than looked up.

That is evidence rather than a guess, which matters because the ledger decides
where the money lands in the P&L.

    python suggest_ledgers_from_zoho.py                     # every vendor missing a ledger
    python suggest_ledgers_from_zoho.py "True Value Textile" "Campus Mall"
    python suggest_ledgers_from_zoho.py --csv ledger_map.csv # write the findings into the map

Vendors with no prior bills cannot be answered this way - nothing has ever been
booked for them - and are reported as such.
"""
import argparse
import asyncio
import csv
import os
import re
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from zoho_client import zoho_books_client

MAX_BILLS_PER_VENDOR = 12


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


async def fetch_all(fetcher, limit_pages=None):
    out, page = [], 1
    while True:
        batch = await fetcher(page=page, per_page=200)
        if not batch:
            break
        out.extend(batch)
        page += 1
        if limit_pages and page > limit_pages:
            break
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendors", nargs="*", help="vendor names; default is every vendor with no ledger")
    ap.add_argument("--csv", default=None, help="write the findings into this ledger map CSV")
    args = ap.parse_args()

    print("Reading Zoho...")
    contacts = await fetch_all(zoho_books_client.get_vendors)
    accounts = await fetch_all(zoho_books_client.get_chartofaccounts)
    bills = await fetch_all(zoho_books_client.get_bills)
    print(f"  {len(contacts)} contacts, {len(accounts)} accounts, {len(bills)} bills")

    account_name = {str(a.get("account_id")): a.get("account_name") for a in accounts}
    contact_by_name = {norm(c.get("contact_name")): c for c in contacts if c.get("contact_name")}

    targets = args.vendors
    if not targets:
        db = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)["invoice_db"]
        rows = await db["vendors"].find(
            {"$or": [{"ledger_id": None}, {"ledger_id": {"$exists": False}}]},
            {"vendor_name": 1, "_id": 0},
        ).to_list(None)
        targets = [r["vendor_name"] for r in rows if r.get("vendor_name")]
        print(f"  {len(targets)} vendor(s) in the database have no ledger")

    bills_by_vendor = {}
    for b in bills:
        bills_by_vendor.setdefault(str(b.get("vendor_id")), []).append(b)

    findings = {}
    print()
    for name in targets:
        contact = contact_by_name.get(norm(name))
        if not contact:
            print(f"{name[:44]:46} not a contact in Zoho")
            continue

        contact_id = str(contact.get("contact_id"))
        vendor_bills = bills_by_vendor.get(contact_id, [])
        if not vendor_bills:
            print(f"{name[:44]:46} no bills in Zoho yet - nothing to learn from")
            continue

        used = Counter()
        for bill in vendor_bills[:MAX_BILLS_PER_VENDOR]:
            detail = await zoho_books_client.get_bill(bill.get("bill_id"))
            for line in (detail or {}).get("line_items", []):
                if line.get("account_id"):
                    used[str(line["account_id"])] += 1

        if not used:
            print(f"{name[:44]:46} {len(vendor_bills)} bill(s), but no account on any line")
            continue

        top_id, count = used.most_common(1)[0]
        total = sum(used.values())
        ledger = account_name.get(top_id, f"(unknown account {top_id})")
        confidence = "certain" if len(used) == 1 else f"{count}/{total} lines"
        findings[name] = ledger
        print(f"{name[:44]:46} {ledger[:38]:40} [{confidence}]")

        if len(used) > 1:
            for acc_id, n in used.most_common()[1:4]:
                print(f"{'':46}   also: {account_name.get(acc_id, acc_id)} ({n})")

    if args.csv and findings:
        path = args.csv
        rows = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                for row in reader:
                    vendor = (row.get("Vendor") or "").strip()
                    # Only fill blanks - never overwrite a decision someone made.
                    if vendor in findings and not (row.get("Ledger") or "").strip():
                        row["Ledger"] = findings[vendor]
                        row["Why"] = "from past Zoho bills"
                    rows.append(row)
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            print(f"\nfilled blank Ledger cells in {path} - existing entries left alone")


if __name__ == "__main__":
    asyncio.run(main())
