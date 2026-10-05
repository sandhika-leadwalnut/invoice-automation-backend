"""
Repair invoice dates that extraction read in the wrong day/month order.

Unstract normalises dates to yyyy-mm-dd using US ordering, so an Indian invoice
dated 01/10/2026 (1 October) is stored as 2026-01-10 (10 January). Every invoice
dated on the 1st to the 12th of a month is at risk; past the 12th there is no
valid US reading, so it comes through correctly. That is why the damage looks
random.

A flip is only repaired when swapping day and month produces a date that is
clearly better: close to the day the invoice arrived, and not in the future.
Anything ambiguous is reported and left alone - a genuinely old invoice must not
be quietly redated.

    python fix_flipped_dates.py
    python fix_flipped_dates.py --apply
"""
import argparse
from datetime import datetime, timedelta

from pymongo import MongoClient, UpdateOne

from config import settings
from verification_router import (
    _parse_invoice_date,
    _add_credit_period,
    _next_payment_cycle,
)

# How close to the arrival date a swapped date must land to be believed.
PLAUSIBLE_WINDOW_DAYS = 60

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="write the corrections")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
db = client["invoice_db"]
invoices, vendors = db["invoices"], db["vendors"]

_vendor_cache = {}


def credit_days_for(data, invoice):
    key = (data.get("vendor_id") or invoice.get("vendor_id"),
           data.get("vendor_gstin") or invoice.get("vendor_gstin"))
    if key not in _vendor_cache:
        vendor = None
        if key[0]:
            vendor = vendors.find_one({"vendor_id": key[0]})
        if not vendor and key[1] and str(key[1]).strip() != settings.company_gstin:
            vendor = vendors.find_one({"gstin": str(key[1]).strip()})
        _vendor_cache[key] = vendor
    vendor = _vendor_cache[key]
    raw = vendor.get("credit_days") if vendor else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def due_date_for(invoice_date, credit_days):
    return _add_credit_period(invoice_date, credit_days) if credit_days else _next_payment_cycle(invoice_date)


docs = list(invoices.find(
    {"deleted_at": {"$exists": False}},
    {"vendor_name": 1, "invoice_number": 1, "invoice_date": 1, "created_at": 1,
     "invoice_data": 1, "accepted_data": 1, "edited_data": 1, "expected_payment_date": 1},
))

repairs, ambiguous = [], []

for d in docs:
    data = d.get("accepted_data") or d.get("edited_data") or d.get("invoice_data") or {}
    raw = d.get("invoice_date") or data.get("invoice_date")
    arrived = d.get("created_at")
    if not raw or not arrived:
        continue

    current = _parse_invoice_date(raw)
    if not current or current.day > 12:
        # Past the 12th there is no valid US reading, so it cannot be flipped.
        continue

    try:
        swapped = current.replace(month=current.day, day=current.month)
    except ValueError:
        continue

    arrival = arrived.date()
    current_gap = abs((arrival - current).days)
    swapped_gap = abs((arrival - swapped).days)

    # Only when the swap is clearly better and lands somewhere believable.
    better = swapped_gap + 30 < current_gap
    believable = swapped <= arrival + timedelta(days=1) and swapped_gap <= PLAUSIBLE_WINDOW_DAYS
    if not (better and believable):
        if current_gap > 90:
            ambiguous.append((d, current, swapped, arrival))
        continue

    credit = credit_days_for(data, d)
    repairs.append({
        "_id": d["_id"],
        "doc": d,
        "vendor": d.get("vendor_name") or data.get("vendor_name") or "Unknown",
        "number": d.get("invoice_number") or data.get("invoice_number") or str(d["_id"])[:8],
        "from": current,
        "to": swapped,
        "arrived": arrival,
        "old_due": d.get("expected_payment_date"),
        "new_due": due_date_for(swapped, credit),
        "credit": credit,
    })

print(f"invoices checked : {len(docs)}")
print(f"flips to repair  : {len(repairs)}")

if repairs:
    print(f"\n  {'VENDOR':30} {'INVOICE':16} {'WAS':12} {'SHOULD BE':12} {'ARRIVED':12} {'OLD DUE':12} NEW DUE")
    for r in repairs:
        print(f"  {str(r['vendor'])[:29]:30} {str(r['number'])[:15]:16} "
              f"{str(r['from']):12} {str(r['to']):12} {str(r['arrived']):12} "
              f"{str(r['old_due'].date()) if r['old_due'] else '-':12} {r['new_due']}")

if ambiguous:
    print(f"\nLEFT ALONE ({len(ambiguous)}) - dated well before arrival, but swapping does not clearly help:")
    for d, current, swapped, arrival in ambiguous:
        print(f"  {str(d.get('vendor_name'))[:29]:30} is {current}, swapped {swapped}, arrived {arrival}")
    print("  Check the PDF for these - they may be genuinely old invoices.")

if not args.apply:
    print("\nDry run. Nothing changed. Re-run with --apply.")
    raise SystemExit(0)

if not repairs:
    print("\nNothing to repair.")
    raise SystemExit(0)

writes = []
for r in repairs:
    fields = {
        "invoice_date": r["to"].isoformat(),
        "expected_payment_date": datetime(r["new_due"].year, r["new_due"].month, r["new_due"].day),
        "invoice_date_corrected": {
            "from": str(r["from"]),
            "to": str(r["to"]),
            "reason": "day/month order swapped by extraction",
            "at": datetime.utcnow(),
        },
    }
    # The nested copies are what the review screen and the payment sheet read.
    # Only touch the ones that actually exist: these fields are null on many
    # records, and Mongo refuses to create a path inside a null.
    for nested in ("invoice_data", "accepted_data", "edited_data"):
        if isinstance(r["doc"].get(nested), dict):
            fields[f"{nested}.invoice_date"] = r["to"].isoformat()
    writes.append(UpdateOne({"_id": r["_id"]}, {"$set": fields}))

result = invoices.bulk_write(writes)
print(f"\ncorrected {result.modified_count} invoice(s)")
print("Each carries an invoice_date_corrected note recording what was changed and why.")
