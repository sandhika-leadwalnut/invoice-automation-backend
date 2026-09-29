"""
Find invoice vendor names that no vendor record matches, and propose aliases.

Invoices carry the vendor's legal name; the vendor master records a working one.
"SUMO TECHNOLOGIES PVT LTD" on the bill, "Sumo Technologies" in the master. The
payment sheet matches exactly - deliberately, because its output is a bank
account number - so the gap is closed by recording the invoice's spelling as an
alias on the vendor.

This reads the real invoice data, finds every unmatched vendor name, and
suggests which vendor each belongs to. YOU confirm before anything is written.

    python suggest_vendor_aliases.py              # report + write a CSV to review
    python suggest_vendor_aliases.py --apply alias_map.csv

The CSV has two columns, Invoice Name and Vendor Name. Delete any row you are
not sure about - a wrong alias means paying the wrong account.
"""
import argparse
import csv
import re
import sys
from collections import Counter

from pymongo import MongoClient

from config import settings

LEGAL_FORMS = ("PRIVATELIMITED", "PVTLTD", "PRIVATELTD", "LIMITED", "LLP",
               "PVT", "LTD", "INC", "CORP", "COMPANY", "OPC")


def norm(value):
    text = re.sub(r"[^A-Z0-9]", "", str(value or "").upper().replace("&", "AND"))
    changed = True
    while changed:
        changed = False
        for form in LEGAL_FORMS:
            if text.endswith(form) and len(text) > len(form) + 4:
                text = text[: -len(form)]
                changed = True
    return text


parser = argparse.ArgumentParser()
parser.add_argument("--apply", metavar="CSV", default=None,
                    help="write the aliases from a reviewed CSV")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
db = client["invoice_db"]
vendors, invoices = db["vendors"], db["invoices"]

if args.apply:
    with open(args.apply, newline="", encoding="utf-8-sig") as f:
        pairs = [(r.get("Invoice Name", "").strip(), r.get("Vendor Name", "").strip())
                 for r in csv.DictReader(f)]
    pairs = [(a, b) for a, b in pairs if a and b]

    written = 0
    for invoice_name, vendor_name in pairs:
        result = vendors.update_one(
            {"vendor_name": re.compile(f"^{re.escape(vendor_name)}$", re.IGNORECASE)},
            {"$addToSet": {"aliases": invoice_name}},
        )
        if result.matched_count:
            written += 1
            print(f"   {vendor_name}  <-  {invoice_name}")
        else:
            print(f"   !! no vendor named {vendor_name!r}, skipped")
    print(f"\nadded {written} alias(es)")
    sys.exit(0)

all_vendors = list(vendors.find({}, {"vendor_name": 1, "aliases": 1, "bank_details": 1, "_id": 0}))
known = {}
for v in all_vendors:
    for name in [v.get("vendor_name")] + list(v.get("aliases") or []):
        if name:
            known.setdefault(norm(name), v)

names = Counter()
for inv in invoices.find({"deleted_at": {"$exists": False}}, {"vendor_name": 1, "status": 1, "_id": 0}):
    if inv.get("vendor_name"):
        names[inv["vendor_name"].strip()] += 1

unmatched = [(n, c) for n, c in names.most_common() if norm(n) not in known]
print(f"distinct vendor names on invoices : {len(names)}")
print(f"already matching a vendor record  : {len(names) - len(unmatched)}")
print(f"unmatched                         : {len(unmatched)}\n")

rows = []
print(f"{'INVOICE NAME':44} {'N':>4}  SUGGESTED VENDOR")
for invoice_name, count in unmatched:
    target = norm(invoice_name)
    hits = [v for k, v in known.items()
            if len(k) >= 5 and (k in target or target in k)]
    seen, unique = set(), []
    for v in hits:
        if v["vendor_name"] not in seen:
            seen.add(v["vendor_name"])
            unique.append(v)

    if len(unique) == 1:
        suggestion = unique[0]["vendor_name"]
        note = "" if unique[0].get("bank_details") else "  (vendor has no bank details)"
    elif unique:
        suggestion = "AMBIGUOUS: " + ", ".join(v["vendor_name"] for v in unique[:3])
        note = ""
    else:
        suggestion = ""
        note = ""
    print(f"{invoice_name[:43]:44} {count:>4}  {suggestion}{note}")
    rows.append([invoice_name, suggestion if not suggestion.startswith("AMBIGUOUS") else "", count])

with open("alias_map.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["Invoice Name", "Vendor Name", "Invoices affected"])
    w.writerows(rows)

print("\nwrote alias_map.csv")
print("Check every row, delete any you are unsure of, then:")
print("   python suggest_vendor_aliases.py --apply alias_map.csv")
