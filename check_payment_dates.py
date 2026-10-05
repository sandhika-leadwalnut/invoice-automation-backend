"""
Find invoices whose expected payment date does not stand up.

The due date is only ever as good as the invoice date it was computed from, and
that date comes out of extraction. A misread date produces a confident, precise,
wrong due date - and nothing currently questions it. Finance then sees a payment
marked 247 days overdue on an invoice that arrived last week.

Four things are checked:

  IMPOSSIBLE    due date before the invoice date
  STALE         invoice dated long before it arrived - usually a misread year
                or a day/month swap, occasionally a genuinely old invoice
  FUTURE        invoice dated after it arrived
  NO DATE       no readable invoice date, so no due date could be worked out

    python check_payment_dates.py
    python check_payment_dates.py --stale-days 120
"""
import argparse
from datetime import datetime, timedelta

from pymongo import MongoClient

from config import settings
from verification_router import _parse_invoice_date

parser = argparse.ArgumentParser()
parser.add_argument("--stale-days", type=int, default=90,
                    help="flag invoices dated more than this many days before they arrived")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
invoices = client["invoice_db"]["invoices"]

docs = list(invoices.find(
    {"deleted_at": {"$exists": False}},
    {"vendor_name": 1, "invoice_number": 1, "invoice_date": 1, "status": 1,
     "expected_payment_date": 1, "created_at": 1, "invoice_data": 1, "accepted_data": 1},
))

impossible, stale, future, no_date, fine = [], [], [], [], 0

for d in docs:
    data = d.get("accepted_data") or d.get("invoice_data") or {}
    raw = d.get("invoice_date") or data.get("invoice_date")
    number = d.get("invoice_number") or data.get("invoice_number") or str(d["_id"])[:8]
    vendor = d.get("vendor_name") or "Unknown"
    arrived = d.get("created_at")
    due = d.get("expected_payment_date")

    invoice_date = _parse_invoice_date(raw)
    if not invoice_date:
        if raw or data:
            no_date.append((vendor, number, raw, arrived))
        continue

    row = (vendor, number, invoice_date, due, arrived, raw)

    if due and due.date() < invoice_date:
        impossible.append(row)
    elif arrived and invoice_date > arrived.date() + timedelta(days=1):
        future.append(row)
    elif arrived and invoice_date < arrived.date() - timedelta(days=args.stale_days):
        stale.append(row)
    else:
        fine += 1


def show(title, rows, note):
    if not rows:
        return
    print(f"\n{title} ({len(rows)})")
    print(f"  {note}")
    print(f"  {'VENDOR':30} {'INVOICE':16} {'INVOICE DATE':13} {'DUE':13} {'ARRIVED':13} RAW")
    for vendor, number, invoice_date, due, arrived, raw in rows[:25]:
        print(f"  {str(vendor)[:29]:30} {str(number)[:15]:16} "
              f"{str(invoice_date):13} {str(due.date()) if due else '-':13} "
              f"{str(arrived.date()) if arrived else '-':13} {raw!r}")
    if len(rows) > 25:
        print(f"  ...and {len(rows) - 25} more")


print(f"invoices checked : {len(docs)}")
print(f"dates look sound : {fine}")

show("IMPOSSIBLE", impossible, "due date falls before the invoice date - the calculation or the data is wrong")
show("DATED IN THE FUTURE", future, "invoice dated after it reached us - almost always a misread date")
show("STALE", stale, f"invoice dated more than {args.stale_days} days before it arrived - check the year and the day/month order")

if no_date:
    print(f"\nNO READABLE INVOICE DATE ({len(no_date)})")
    print("  nothing to compute a due date from")
    print(f"  {'VENDOR':30} {'INVOICE':16} RAW VALUE")
    for vendor, number, raw, arrived in no_date[:15]:
        print(f"  {str(vendor)[:29]:30} {str(number)[:15]:16} {raw!r}")

print("\nOpen the PDF for anything listed above and compare it with the raw value.")
print("If extraction read it wrong, correct the invoice in the review screen and")
print("re-accept it - the due date is recalculated from whatever is corrected.")
