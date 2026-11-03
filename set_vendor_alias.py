"""
Add or remove a trading name on a vendor, so invoices under that name match.

Vendors do not always bill under the name we know them by. INNOVATECTURE DESIGN
SERVICES LLP invoices the managed office as "Royal Lotus View", so the name on
the invoice matches nothing and the bill ends up against the wrong contact.

An alias fixes that at the right level: one vendor, one bank account, one
ledger, several names it may bill under.

Aliases are matched exactly, after normalising case, punctuation and the usual
legal suffixes - never fuzzily. A similarity score should not decide which bank
account gets paid.

    python set_vendor_alias.py "INNOVATECTURE DESIGN SERVICES LLP" --add "Royal Lotus View"
    python set_vendor_alias.py "INNOVATECTURE DESIGN SERVICES LLP" --add "Royal Lotus View" --apply

    python set_vendor_alias.py "INNOVATECTURE DESIGN SERVICES LLP"     # just show them
    python set_vendor_alias.py "..." --remove "Royal Lotus View" --apply
"""
import argparse
import re
import sys

from pymongo import MongoClient

from config import settings
from verification_router import _norm_vendor_name

parser = argparse.ArgumentParser()
parser.add_argument("vendor", help="vendor name (case-insensitive) or vendor_id")
parser.add_argument("--add", action="append", default=[], help="a name this vendor also bills under")
parser.add_argument("--remove", action="append", default=[], help="an alias to drop")
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
vendors = client["invoice_db"]["vendors"]

doc = vendors.find_one({"vendor_id": args.vendor})
if not doc:
    matches = list(vendors.find(
        {"vendor_name": re.compile(f"^{re.escape(args.vendor)}$", re.IGNORECASE)}))
    if len(matches) > 1:
        print(f"!! {len(matches)} vendors are named {args.vendor!r}. Use a vendor_id:")
        for m in matches:
            print(f"     {m.get('vendor_id')}")
        sys.exit(1)
    if not matches:
        loose = list(vendors.find(
            {"vendor_name": re.compile(re.escape(args.vendor), re.IGNORECASE)}))
        print(f"!! No vendor matches {args.vendor!r}.")
        for m in loose[:8]:
            print(f"   did you mean: {m.get('vendor_name')}")
        sys.exit(1)
    doc = matches[0]

current = list(doc.get("aliases") or [])

print(f"VENDOR : {doc.get('vendor_name')}   ({doc.get('vendor_id')})")
print(f"   aliases now: {', '.join(current) if current else '(none)'}")

# An alias that collides with another vendor is the thing to catch here: two
# vendors answering to one name means an invoice could go to either, and which
# one it picks decides whose bank account is paid.
everyone = list(vendors.find({}, {"vendor_name": 1, "aliases": 1, "vendor_id": 1}))

updated = list(current)
problems = []

for alias in args.add:
    alias = alias.strip()
    if not alias:
        continue
    target = _norm_vendor_name(alias)

    if target == _norm_vendor_name(doc.get("vendor_name")):
        problems.append(f"{alias!r} is already this vendor's own name")
        continue
    if any(_norm_vendor_name(a) == target for a in updated):
        problems.append(f"{alias!r} is already an alias here")
        continue

    clash = next(
        (v for v in everyone
         if v.get("vendor_id") != doc.get("vendor_id")
         and target in [_norm_vendor_name(v.get("vendor_name"))]
         + [_norm_vendor_name(a) for a in (v.get("aliases") or [])]),
        None,
    )
    if clash:
        problems.append(
            f"{alias!r} already belongs to {clash.get('vendor_name')!r} - "
            f"two vendors cannot answer to one name"
        )
        continue

    updated.append(alias)
    print(f"   + {alias}")

for alias in args.remove:
    target = _norm_vendor_name(alias)
    before = len(updated)
    updated = [a for a in updated if _norm_vendor_name(a) != target]
    if len(updated) == before:
        problems.append(f"{alias!r} is not an alias here")
    else:
        print(f"   - {alias}")

if problems:
    print()
    for problem in problems:
        print(f"!! {problem}")

if updated == current:
    print("\nNothing to change.")
    sys.exit(1 if problems else 0)

print(f"\n   aliases after: {', '.join(updated) if updated else '(none)'}")

if not args.apply:
    print("\nDry run. Nothing written. Re-run with --apply.")
    sys.exit(0)

vendors.update_one({"vendor_id": doc["vendor_id"]}, {"$set": {"aliases": updated}})
print("\nSaved.")
print("\nThis changes how invoices are matched from now on. Anything already")
print("pushed to Zoho under the wrong vendor stays there - that has to be")
print("corrected in Zoho.")
