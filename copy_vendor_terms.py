"""
Copy payment terms from one vendor record to another.

The same real-world vendor sometimes has more than one record, because the name
comes out of extraction differently ("Mohammad Kaiser" and "MOHAMMAD KAISER
PERWEZ" are one person). Only one of them matches the vendor master, so only one
gets a credit period and bank details - and which record an invoice lands on
then decides whether it gets a sensible payment date.

This copies ONLY the payment terms:

    credit_days, tds_rate, bank_details

Identity fields - vendor_id, zoho_contact_id, ledger_id, gstin - are never
touched. Those must stay distinct or invoices would start resolving to the wrong
Zoho contact.

    python copy_vendor_terms.py --from "MOHAMMAD KAISER PERWEZ" --to "Mohammad Kaiser"
    python copy_vendor_terms.py --from "..." --to "..." --apply

Either side can be given as a vendor_id instead of a name.
"""
import argparse
import re
import sys

from pymongo import MongoClient

from config import settings

TERM_FIELDS = ["credit_days", "tds_rate", "bank_details"]

parser = argparse.ArgumentParser()
parser.add_argument("--from", dest="source", required=True, help="vendor name or vendor_id to copy FROM")
parser.add_argument("--to", dest="target", required=True, help="vendor name or vendor_id to copy TO")
parser.add_argument("--apply", action="store_true", help="write the change")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
vendors = client["invoice_db"]["vendors"]


def find_one(value, label):
    doc = vendors.find_one({"vendor_id": value})
    if doc:
        return doc

    exact = re.compile(f"^{re.escape(value.strip())}$", re.IGNORECASE)
    matches = list(vendors.find({"vendor_name": exact}))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"!! {label}: {len(matches)} vendors are named {value!r}. Use a vendor_id instead:")
        for m in matches:
            print(f"     {m.get('vendor_id')}")
        sys.exit(1)

    loose = list(vendors.find({"vendor_name": re.compile(re.escape(value.strip()), re.IGNORECASE)}))
    print(f"!! {label}: no vendor matches {value!r} exactly.")
    if loose:
        print("   Did you mean:")
        for m in loose[:8]:
            print(f"     {m.get('vendor_name')}   ({m.get('vendor_id')})")
    sys.exit(1)


source = find_one(args.source, "--from")
target = find_one(args.target, "--to")

if source.get("vendor_id") == target.get("vendor_id"):
    print("!! Source and target are the same record. Nothing to do.")
    sys.exit(1)

terms = {f: source[f] for f in TERM_FIELDS if source.get(f) is not None}
if not terms:
    print(f"!! {source.get('vendor_name')!r} has no payment terms to copy.")
    sys.exit(1)

print(f"FROM : {source.get('vendor_name')}   ({source.get('vendor_id')})")
print(f"TO   : {target.get('vendor_name')}   ({target.get('vendor_id')})")
print()
print("Terms to copy:")
for field, value in terms.items():
    current = target.get(field)
    marker = "unchanged" if current == value else (f"replaces {current!r}" if current is not None else "new")
    print(f"   {field} = {value}   [{marker}]")

if not args.apply:
    print()
    print("Dry run. Nothing changed. Re-run with --apply to write it.")
    sys.exit(0)

result = vendors.update_one({"vendor_id": target["vendor_id"]}, {"$set": terms})
print()
print(f"matched {result.matched_count}, modified {result.modified_count}")
print("Identity fields (vendor_id, zoho_contact_id, ledger_id, gstin) were not touched.")
