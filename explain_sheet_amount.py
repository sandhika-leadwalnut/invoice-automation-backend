"""
Show exactly how the payment sheet arrived at an amount.

"This figure is wrong" is hard to act on, because the number has several
inputs: the extracted subtotal and tax, the total, the vendor's TDS rate, and
which of those the taxable value was taken from. This prints all of them for
one invoice, and checks that the extracted figures add up.

That last check is usually the answer. If subtotal + tax does not equal the
total, extraction misread the invoice, and every figure derived from it - the
TDS, the net, the payment - is wrong downstream of that.

    python explain_sheet_amount.py "2026-27/D-06"
    python explain_sheet_amount.py "PF-2026-OCT-1001" "PF-2026-OCT-1002"
    python explain_sheet_amount.py --vendor "Prodbrew"
"""
import argparse
import re
import sys

from pymongo import MongoClient

from config import settings
from verification_router import (
    _norm_amount, _norm_vendor_name, _taxable_value, _tds_for,
)

NOT_DELETED = {"deleted_at": {"$exists": False}}
TAX_FIELDS = ("subtotal", "cgst", "sgst", "igst", "cess", "tax_total", "total_amount")


def key(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def scan():
    """
    Every payable invoice whose extracted figures do not reconcile.

    Two different things look identical here and the difference is money:
    a vendor who withholds TDS and bills the net (handled - nothing further is
    deducted), and an invoice extraction simply misread (not handled - every
    figure built on it is wrong). The gap matching the TDS exactly is what
    tells them apart, so both are shown with that comparison made.
    """
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    db = client["invoice_db"]
    invoices, vendors = db["invoices"], db["vendors"]

    by_name = {}
    for v in vendors.find({}, {"vendor_name": 1, "aliases": 1, "tds_rate": 1}):
        for n in [v.get("vendor_name")] + list(v.get("aliases") or []):
            if n:
                by_name.setdefault(_norm_vendor_name(n), v)

    withheld, misread = [], []

    for doc in invoices.find({"status": {"$in": ["accepted", "pending", "edited"]},
                              **NOT_DELETED}):
        data = doc.get("accepted_data") or doc.get("edited_data") or doc.get("invoice_data") or {}
        if not isinstance(data, dict):
            continue

        subtotal = _norm_amount(data.get("subtotal"))
        total = _norm_amount(data.get("total_amount")) or _norm_amount(doc.get("total_amount"))
        tax = None
        for field in ("tax_total", "total_tax", "gst_total"):
            tax = _norm_amount(data.get(field))
            if tax:
                break
        if not tax:
            parts = [_norm_amount(data.get(f)) for f in ("cgst", "sgst", "igst", "cess")]
            tax = sum(p for p in parts if p) or None

        if not (subtotal and tax and total):
            continue

        gap = round(subtotal + tax - total, 2)
        if abs(gap) <= 1:
            continue

        name = doc.get("vendor_name") or data.get("vendor_name") or "?"
        vendor = by_name.get(_norm_vendor_name(name))
        rate = float((vendor or {}).get("tds_rate") or 0)
        expected_tds = round(subtotal * rate) if rate else 0

        row = (name, data.get("invoice_number") or doc.get("invoice_number"),
               total, gap, expected_tds, doc.get("status"))
        (withheld if expected_tds and abs(gap - expected_tds) <= 2 else misread).append(row)

    if withheld:
        print(f"TDS ALREADY DEDUCTED BY THE VENDOR ({len(withheld)}) - handled, "
              f"nothing further comes off:")
        for name, number, total, gap, tds, status in withheld:
            print(f"   {name[:28]:30} {str(number)[:20]:22} total {total:>12}  "
                  f"withheld {gap:>9}  [{status}]")
        print()

    if misread:
        print(f"FIGURES DO NOT ADD UP ({len(misread)}) - check these against the PDF:")
        for name, number, total, gap, tds, status in misread:
            expected = f", TDS would be {tds}" if tds else ""
            print(f"   {name[:28]:30} {str(number)[:20]:22} total {total:>12}  "
                  f"off by {gap:>9}{expected}  [{status}]")
        print("\n   A gap that is not the TDS is a discount, a rounding line, or")
        print("   extraction having misread the invoice. Only the PDF says which.")

    if not withheld and not misread:
        print("Every payable invoice reconciles.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("numbers", nargs="*", help="invoice number(s)")
    ap.add_argument("--vendor", default=None, help="every invoice for this vendor")
    ap.add_argument("--scan", action="store_true",
                    help="check every payable invoice and list only the ones whose "
                         "figures do not add up")
    args = ap.parse_args()

    if not args.numbers and not args.vendor and not args.scan:
        print(__doc__)
        sys.exit(1)

    if args.scan:
        scan()
        return

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    db = client["invoice_db"]
    invoices, vendors = db["invoices"], db["vendors"]

    query = dict(NOT_DELETED)
    if args.vendor:
        query["vendor_name"] = re.compile(re.escape(args.vendor), re.IGNORECASE)

    wanted = {key(n) for n in args.numbers}
    docs = []
    for doc in invoices.find(query):
        data = doc.get("accepted_data") or doc.get("edited_data") or doc.get("invoice_data") or {}
        number = doc.get("invoice_number") or data.get("invoice_number")
        if not wanted or key(number) in wanted:
            docs.append((doc, data, number))

    if not docs:
        print("Nothing matched.")
        return

    for doc, data, number in docs:
        name = doc.get("vendor_name") or data.get("vendor_name") or "?"
        print("=" * 68)
        print(f"{name}   invoice {number or '?'}")
        print(f"   id {doc['_id']}   status {doc.get('status')}")

        print("\n   EXTRACTED FROM THE INVOICE")
        values = {}
        for field in TAX_FIELDS:
            raw = data.get(field)
            if raw is None:
                raw = doc.get(field)
            values[field] = _norm_amount(raw)
            if raw is not None:
                print(f"      {field:14} {raw}")
        missing = [f for f in TAX_FIELDS if values.get(f) is None]
        if missing:
            print(f"      (not extracted: {', '.join(missing)})")

        # The reconciliation. Extraction reads these fields independently, so
        # nothing forces them to agree - and when they do not, the invoice was
        # misread and every figure built on it is wrong.
        total = values.get("total_amount")
        subtotal = values.get("subtotal")
        tax = values.get("tax_total")
        if tax is None:
            parts = [values.get(f) for f in ("cgst", "sgst", "igst", "cess")]
            tax = sum(p for p in parts if p) or None

        print("\n   DO THE FIGURES ADD UP?")
        if subtotal is not None and tax is not None and total is not None:
            expected = round(subtotal + tax, 2)
            if abs(expected - total) <= 1:
                print(f"      {subtotal} + {tax} = {expected}, and the total says {total}  OK")
            else:
                print(f"      {subtotal} + {tax} = {expected}, but the total says {total}")
                print(f"      OFF BY {round(total - expected, 2)} - extraction misread this invoice.")
                print("      Everything below is derived from these figures, so it is")
                print("      wrong too. Fix the invoice data before the amount.")
        else:
            print("      Cannot check - subtotal, tax or total was not extracted.")

        # The vendor, by the same ladder the sheet uses.
        vendor = None
        vendor_id = doc.get("vendor_id") or data.get("vendor_id")
        if vendor_id:
            vendor = vendors.find_one({"vendor_id": vendor_id})
        if not vendor:
            target = _norm_vendor_name(name)
            for v in vendors.find({}, {"vendor_name": 1, "aliases": 1, "tds_rate": 1,
                                       "vendor_id": 1, "bank_details": 1}):
                names = [v.get("vendor_name")] + list(v.get("aliases") or [])
                if any(_norm_vendor_name(n) == target for n in names):
                    vendor = v
                    break

        print("\n   VENDOR MASTER")
        if vendor:
            rate = vendor.get("tds_rate")
            print(f"      matched       {vendor.get('vendor_name')}")
            print(f"      tds_rate      {rate if rate is not None else '(none - nothing deducted)'}"
                  f"{f'  = {float(rate):.0%}' if rate else ''}")
        else:
            print(f"      NO MATCH for {name!r} - no TDS deducted, no bank details")

        print("\n   WHAT THE SHEET PUTS IN COLUMN H")
        amount = total
        if amount is None:
            print("      no amount on the invoice, so the row is left blank")
            print()
            continue

        taxable, basis = _taxable_value(data, amount)
        tds, _ = _tds_for(doc, data, vendor, amount)
        print(f"      invoice total {amount}")
        print(f"      taxable value {taxable}   (from: {basis})")
        print(f"      TDS           {tds}")
        print(f"      PAID          {round(amount - tds, 2)}")
        print()


if __name__ == "__main__":
    main()
