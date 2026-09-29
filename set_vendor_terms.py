"""
Set payment terms on a vendor directly.

For vendors that exist in invoice_db.vendors but are not in the master sheet,
so credit days and bank details have nowhere else to come from. Gopikanna,
FatJoe, TrioSEO, LinkedIn, Ahrefs and about thirty others are in this position.

Only touches payment terms - credit_days, tds_rate, bank_details. Identity
fields (vendor_id, zoho_contact_id, ledger_id, gstin) are never written, because
getting those wrong sends invoices to the wrong Zoho contact.

    python set_vendor_terms.py "Gopikanna" --credit-days 7
    python set_vendor_terms.py "Gopikanna" --credit-days 7 \\
        --bank "HDFC Bank" --ifsc HDFC0001388 --account 50100723881009
    python set_vendor_terms.py "Gopikanna" --credit-days 7 --apply

The vendor can be given by name (case-insensitive) or vendor_id.

A note on the sheet: anything set here is invisible to the master sheet, so the
next time someone rebuilds vendors from it these values will not be there. The
durable fix is a row in the master sheet - this is for unblocking one vendor.
"""
import argparse
import re
import sys

from pymongo import MongoClient

from config import settings

# 4 letters, a zero, then 6 alphanumerics - e.g. HDFC0001388.
IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

parser = argparse.ArgumentParser()
parser.add_argument("vendor", help="vendor name (case-insensitive) or vendor_id")
parser.add_argument("--credit-days", type=int, default=None)
parser.add_argument("--tds-rate", type=float, default=None, help="as a decimal, e.g. 0.1 for 10%%")
parser.add_argument("--bank", default=None, help="bank name, e.g. \"HDFC Bank\"")
parser.add_argument("--ifsc", default=None)
parser.add_argument("--account", default=None, help="account number - quote it to keep leading zeros")
parser.add_argument("--apply", action="store_true", help="write the change")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
vendors = client["invoice_db"]["vendors"]

doc = vendors.find_one({"vendor_id": args.vendor})
if not doc:
    matches = list(vendors.find({"vendor_name": re.compile(f"^{re.escape(args.vendor)}$", re.IGNORECASE)}))
    if len(matches) > 1:
        print(f"!! {len(matches)} vendors are named {args.vendor!r}. Use a vendor_id:")
        for m in matches:
            print(f"     {m.get('vendor_id')}")
        sys.exit(1)
    if not matches:
        loose = list(vendors.find({"vendor_name": re.compile(re.escape(args.vendor), re.IGNORECASE)}))
        print(f"!! No vendor matches {args.vendor!r}.")
        if loose:
            print("   Did you mean:")
            for m in loose[:8]:
                print(f"     {m.get('vendor_name')}   ({m.get('vendor_id')})")
        sys.exit(1)
    doc = matches[0]

terms = {}
if args.credit_days is not None:
    if args.credit_days < 0:
        print("!! credit days cannot be negative")
        sys.exit(1)
    terms["credit_days"] = args.credit_days
if args.tds_rate is not None:
    terms["tds_rate"] = round(args.tds_rate, 4)

bank = {}
if args.bank:
    bank["bank"] = args.bank.strip()
if args.ifsc:
    ifsc = args.ifsc.strip().upper()
    if not IFSC_PATTERN.match(ifsc):
        print(f"!! {ifsc!r} is not a valid IFSC (4 letters, a zero, then 6 characters).")
        sys.exit(1)
    bank["ifsc"] = ifsc
if args.account:
    # Kept as text so leading zeros survive - a mangled account number is a
    # failed payment, and Excel has already eaten some of these once.
    account = re.sub(r"\s+", "", str(args.account).strip())
    if not account.isalnum():
        print(f"!! {account!r} does not look like an account number")
        sys.exit(1)
    bank["account_number"] = account

if bank:
    existing = doc.get("bank_details") or {}
    merged = {**existing, **bank}
    missing = [f for f in ("bank", "ifsc", "account_number") if not merged.get(f)]
    if missing:
        print(f"!! bank details would be incomplete - missing {', '.join(missing)}.")
        print("   A payment sheet row needs all three. Give them together.")
        sys.exit(1)
    terms["bank_details"] = merged

if not terms:
    print("!! Nothing to set. Give --credit-days, --tds-rate, or the three bank options.")
    sys.exit(1)

print(f"VENDOR : {doc.get('vendor_name')}   ({doc.get('vendor_id')})")
print()
for field, value in terms.items():
    current = doc.get(field)
    state = "unchanged" if current == value else (f"replaces {current!r}" if current is not None else "new")
    print(f"   {field} = {value}   [{state}]")

if not args.apply:
    print()
    print("Dry run. Nothing changed. Re-run with --apply to write it.")
    sys.exit(0)

result = vendors.update_one({"vendor_id": doc["vendor_id"]}, {"$set": terms})
print()
print(f"matched {result.matched_count}, modified {result.modified_count}")
print("Identity fields were not touched.")
