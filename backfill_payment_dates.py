"""
Work out expected_payment_date for invoices already in the database.

Until now the date was only calculated when an invoice was accepted, so every
pending invoice shows a blank. This fills them in using exactly the same
arithmetic the service uses - the date helpers are imported from
verification_router rather than reimplemented, so the two cannot drift apart.

It also promotes invoice_number, invoice_date and total_amount to the top level
for older records, which is what the payment sheet reads.

Invoices accepted before the payload was retained have no invoice_data left at
all; there is nothing to compute from and they are reported, not guessed at.

    python backfill_payment_dates.py            # show what would change
    python backfill_payment_dates.py --apply    # actually change it
"""
import sys
from datetime import datetime

from pymongo import MongoClient, UpdateOne

from config import settings
from verification_router import (
    _parse_invoice_date,
    _add_credit_period,
    _next_payment_cycle,
)

apply_changes = "--apply" in sys.argv

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
db = client["invoice_db"]
invoices = db["invoices"]
vendors = db["vendors"]

# Cached so a vendor is looked up once rather than once per invoice.
_vendor_cache = {}


def find_vendor(data, invoice):
    key = (data.get("vendor_id"), data.get("vendor_gstin") or invoice.get("vendor_gstin"))
    if key in _vendor_cache:
        return _vendor_cache[key]

    vendor = None
    if key[0]:
        vendor = vendors.find_one({"vendor_id": key[0]})
    if not vendor and key[1]:
        vendor = vendors.find_one({"gstin": str(key[1]).strip()})
    _vendor_cache[key] = vendor
    return vendor


candidates = list(invoices.find({
    "deleted_at": {"$exists": False},
    "$or": [
        {"expected_payment_date": {"$exists": False}},
        {"expected_payment_date": None},
    ],
}))

print(f"invoices with no expected payment date : {len(candidates)}")

writes = []
by_credit, by_cycle, no_data, no_date = 0, 0, [], []

for invoice in candidates:
    data = invoice.get("accepted_data") or invoice.get("edited_data") or invoice.get("invoice_data")
    if not data:
        no_data.append(invoice.get("vendor_name") or str(invoice["_id"])[:8])
        continue

    invoice_date = _parse_invoice_date(data.get("invoice_date"))
    if not invoice_date:
        no_date.append(invoice.get("vendor_name") or str(invoice["_id"])[:8])
        continue

    vendor = find_vendor(data, invoice)
    raw = vendor.get("credit_days") if vendor else None
    credit_days = None
    if raw is not None:
        try:
            credit_days = int(raw)
        except (TypeError, ValueError):
            credit_days = None

    if credit_days:
        due = _add_credit_period(invoice_date, credit_days)
        by_credit += 1
    else:
        due = _next_payment_cycle(invoice_date)
        by_cycle += 1

    if not due:
        continue

    fields = {"expected_payment_date": datetime(due.year, due.month, due.day)}
    # Older records kept these only inside invoice_data. vendor_id matters most:
    # it is the one field that identifies the vendor exactly, for every vendor,
    # whether or not they have a GSTIN.
    for key in ("invoice_number", "invoice_date", "total_amount", "vendor_gstin",
                "vendor_id", "zoho_contact_id"):
        if invoice.get(key) is None and data.get(key) is not None:
            fields[key] = data.get(key)

    writes.append(UpdateOne({"_id": invoice["_id"]}, {"$set": fields}))

print()
print(f"  datable                    : {len(writes)}")
print(f"    from a credit period     : {by_credit}")
print(f"    from the 5th/20th cycle  : {by_cycle}")
print(f"  no invoice data left       : {len(no_data)}")
print(f"  unreadable invoice date    : {len(no_date)}")

if no_data:
    print()
    print("  These were accepted before invoice data was retained. Nothing to")
    print("  compute from, so they stay blank:")
    for name in no_data[:10]:
        print(f"     {name}")
    if len(no_data) > 10:
        print(f"     ...and {len(no_data) - 10} more")

if not apply_changes:
    print()
    print("Dry run. Nothing changed. Re-run with --apply to write it.")
    sys.exit(0)

if not writes:
    print("\nNothing to write.")
    sys.exit(0)

result = invoices.bulk_write(writes)
print(f"\nmodified {result.modified_count} invoice(s)")
