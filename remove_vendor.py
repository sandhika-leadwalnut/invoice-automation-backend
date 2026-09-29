"""
Remove a vendor from BOTH the database and Vendor-Ledgers.xlsx.

Deleting from only one of them is how the two drift apart - and because
add_missing_vendors.py --push reads the workbook, a vendor deleted from Mongo
alone comes straight back on the next push. That has already happened once with
Jake Canning and V SATYA SUJATA.

    python remove_vendor.py "Jake Canning"
    python remove_vendor.py "Jake Canning" "V SATYA SUJATA" --apply
    python remove_vendor.py a7827883-f1fb-4937-8d7b-ac6747d0724f --apply

Invoices already recorded against the vendor are untouched - this only removes
the vendor record itself.
"""
import argparse
import os
import re
import shutil
import sys
from datetime import datetime

import openpyxl
from pymongo import MongoClient

from config import settings

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_WORKBOOK = os.path.join(HERE, "Vendor-Ledgers.xlsx")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


parser = argparse.ArgumentParser()
parser.add_argument("vendors", nargs="+", help="vendor names (case-insensitive) or vendor_ids")
parser.add_argument("--apply", action="store_true", help="actually remove")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
vendors = client["invoice_db"]["vendors"]
invoices = client["invoice_db"]["invoices"]

targets, unmatched = [], []
for wanted in args.vendors:
    doc = vendors.find_one({"vendor_id": wanted})
    if not doc:
        matches = list(vendors.find({"vendor_name": re.compile(f"^{re.escape(wanted)}$", re.IGNORECASE)}))
        if len(matches) > 1:
            print(f"!! {len(matches)} vendors named {wanted!r}. Use a vendor_id:")
            for m in matches:
                print(f"     {m.get('vendor_id')}")
            sys.exit(1)
        doc = matches[0] if matches else None
    if doc:
        targets.append(doc)
    else:
        unmatched.append(wanted)

if unmatched:
    print(f"!! not found in the database: {', '.join(unmatched)}")

if not targets:
    print("Nothing to remove.")
    sys.exit(1)

print(f"{'VENDOR':44} {'VENDOR_ID':38} INVOICES")
for doc in targets:
    count = invoices.count_documents({"vendor_name": doc.get("vendor_name")})
    print(f"{str(doc.get('vendor_name'))[:43]:44} {str(doc.get('vendor_id')):38} {count}")

ids = {d.get("vendor_id") for d in targets}
names = {norm(d.get("vendor_name")) for d in targets}

workbook = openpyxl.load_workbook(LEDGER_WORKBOOK)
sheet = workbook["Sheet1"]
doomed = [
    row for row in range(2, sheet.max_row + 1)
    if sheet.cell(row=row, column=1).value
    and (str(sheet.cell(row=row, column=3).value) in ids
         or norm(sheet.cell(row=row, column=1).value) in names)
]
print(f"\nrows to remove from Vendor-Ledgers.xlsx : {len(doomed)}")

if not args.apply:
    print("\nDry run. Nothing removed. Re-run with --apply.")
    sys.exit(0)

removed = vendors.delete_many({"vendor_id": {"$in": list(ids)}}).deleted_count
print(f"\nremoved {removed} from invoice_db.vendors")

if doomed:
    backup = LEDGER_WORKBOOK.replace(".xlsx", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    shutil.copy2(LEDGER_WORKBOOK, backup)
    # Bottom up, so earlier row numbers stay valid as rows shift.
    for row in sorted(doomed, reverse=True):
        sheet.delete_rows(row)
    workbook.save(LEDGER_WORKBOOK)
    print(f"removed {len(doomed)} row(s) from Vendor-Ledgers.xlsx (backed up first)")

print("\nBoth are now in step, so a later --push will not bring them back.")
