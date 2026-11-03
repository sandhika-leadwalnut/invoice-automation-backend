"""
Tests for the expected payment date arithmetic.

Runs against the real functions in verification_router. No database needed -
_add_credit_period is pure.

    python test_payment_date.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verification_router import _add_credit_period, _parse_invoice_date  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected}  ({expected.strftime('%A')})")
        print(f"        got      {actual}  ({actual.strftime('%A')})")
        failures.append(label)


D = datetime.date
INVOICE = D(2026, 9, 10)  # a Thursday - the example Finance gave

print(f"\nInvoice date {INVOICE} is a {INVOICE.strftime('%A')}")

print("\n1. Working-days basis")
check("+15 working days",  _add_credit_period(INVOICE, 15, "business"), D(2026, 10, 1))
check("+30 working days",  _add_credit_period(INVOICE, 30, "business"), D(2026, 10, 22))
check("+7 working days",   _add_credit_period(INVOICE, 7,  "business"), D(2026, 9, 21))
check("+1 working day",    _add_credit_period(INVOICE, 1,  "business"), D(2026, 9, 11))
check("Friday +1 skips the weekend",
      _add_credit_period(D(2026, 9, 11), 1, "business"), D(2026, 9, 14))

print("\n2. Calendar basis (rolls off weekends only)")
check("+15 calendar days lands on a Friday, no roll",
      _add_credit_period(INVOICE, 15, "calendar"), D(2026, 9, 25))
check("+16 lands on Saturday, rolls to Monday",
      _add_credit_period(INVOICE, 16, "calendar"), D(2026, 9, 28))
check("+17 lands on Sunday, rolls to Monday",
      _add_credit_period(INVOICE, 17, "calendar"), D(2026, 9, 28))
check("+30 calendar days",
      _add_credit_period(INVOICE, 30, "calendar"), D(2026, 10, 12))

print("\n3. A due date is never a weekend, on either basis")
ok = True
for days in range(1, 121):
    for basis in ("business", "calendar"):
        if _add_credit_period(INVOICE, days, basis).weekday() >= 5:
            ok = False
print(f"  {'PASS' if ok else 'FAIL'}  1-120 days, both bases, never Sat/Sun")
if not ok:
    failures.append("weekend leaked through")

print("\n4. The two bases differ enough to matter")
for days in (7, 15, 30, 45):
    b = _add_credit_period(INVOICE, days, "business")
    c = _add_credit_period(INVOICE, days, "calendar")
    print(f"        {days:>3} days -> working: {b}   calendar: {c}   gap: {(b - c).days} days")

print("\n5. Invoice date parsing feeds this correctly")
check("ISO from the database", _parse_invoice_date("2026-09-10"), INVOICE)
check("dd/mm/yyyy", _parse_invoice_date("10/09/2026"), INVOICE)
check("dd-mm-yyyy", _parse_invoice_date("10-09-2026"), INVOICE)

print("\n6. Payment cycle for vendors with no credit period")
from verification_router import _next_payment_cycle  # noqa: E402

check("25th -> 5th of NEXT month (Finance's example)",
      _next_payment_cycle(D(2026, 9, 25)), D(2026, 10, 5))
check("6th -> 5th of the SAME month (Finance's example)",
      _next_payment_cycle(D(2026, 9, 6)), D(2026, 9, 5))
check("on the 5th itself", _next_payment_cycle(D(2026, 9, 5)), D(2026, 9, 5))
check("8th is the last day inside the grace window",
      _next_payment_cycle(D(2026, 9, 8)), D(2026, 9, 5))
check("9th has missed it, waits for the 20th",
      _next_payment_cycle(D(2026, 9, 9)), D(2026, 9, 20))
check("23rd still catches the 20th",
      _next_payment_cycle(D(2026, 9, 23)), D(2026, 9, 20))
check("24th has missed it, waits for the 5th",
      _next_payment_cycle(D(2026, 9, 24)), D(2026, 10, 5))
check("1st waits for the 5th", _next_payment_cycle(D(2026, 9, 1)), D(2026, 9, 5))
check("December rolls into January",
      _next_payment_cycle(D(2026, 12, 25)), D(2027, 1, 5))
check("22 Dec catches the 20th", _next_payment_cycle(D(2026, 12, 22)), D(2026, 12, 20))
check("31 Jan -> 5 Feb", _next_payment_cycle(D(2027, 1, 31)), D(2027, 2, 5))

ok = all(_next_payment_cycle(D(2026, 9, d)).day in (5, 20) for d in range(1, 31))
print(f"  {'PASS' if ok else 'FAIL'}  every day of a month lands on a 5th or a 20th")
if not ok:
    failures.append("cycle produced a non-cycle day")

print("\nPayment sheet run date - one date for the whole file")
from datetime import date  # noqa: E402
from verification_router import _payment_run_date  # noqa: E402

for downloaded, expected, why in [
    (date(2026, 11, 1),  date(2026, 11, 5),  "before the 5th"),
    (date(2026, 11, 5),  date(2026, 11, 5),  "ON the 5th - still this cycle"),
    (date(2026, 11, 6),  date(2026, 11, 20), "day after the 5th"),
    (date(2026, 11, 19), date(2026, 11, 20), "before the 20th"),
    (date(2026, 11, 20), date(2026, 11, 20), "ON the 20th - still this cycle"),
    (date(2026, 11, 21), date(2026, 12, 5),  "day after the 20th"),
    (date(2026, 11, 30), date(2026, 12, 5),  "end of month"),
    (date(2026, 12, 31), date(2027, 1, 5),   "crosses the year"),
    (date(2026, 1, 31),  date(2026, 2, 5),   "into a short month"),
    (date(2026, 2, 28),  date(2026, 3, 5),   "out of February"),
]:
    actual = _payment_run_date(downloaded)
    check(f"downloaded {downloaded} ({why})", actual, expected)

print("\nTDS on the payment sheet - deducted on the value excluding GST")
from verification_router import _tds_for, _taxable_value  # noqa: E402

# Gopikanna, invoice 2757: subtotal 313500 + IGST 56430 = 369930.
WITH_GST = {"subtotal": 313500, "igst": 56430, "tax_total": 56430, "total_amount": 369930}

check("TDS is 10% of the subtotal, not the total",
      _tds_for({}, WITH_GST, {"tds_rate": 0.1}, 369930.0)[0], 31350.0)
check("taxable value comes off the subtotal",
      _taxable_value(WITH_GST, 369930.0), (313500.0, "subtotal"))
check("falls back to total less tax_total",
      _taxable_value({"tax_total": 56430}, 369930.0), (313500.0, "total less tax"))
check("falls back to total less the GST components",
      _taxable_value({"cgst": 4680, "sgst": 4680}, 61360.0), (52000.0, "total less GST components"))
check("no tax on the invoice means the total is the taxable value",
      _taxable_value({}, 50000.0), (50000.0, "assumed no GST"))
check("and that basis is reported, not hidden",
      _tds_for({}, {}, {"tds_rate": 0.1}, 50000.0)[1], "assumed no GST")

check("no rate on the vendor deducts nothing",
      _tds_for({}, WITH_GST, {}, 369930.0)[0], 0.0)
check("no vendor at all deducts nothing",
      _tds_for({}, WITH_GST, None, 369930.0)[0], 0.0)
check("a rate of zero deducts nothing",
      _tds_for({}, WITH_GST, {"tds_rate": 0}, 369930.0)[0], 0.0)
check("a junk rate deducts nothing rather than throwing",
      _tds_for({}, WITH_GST, {"tds_rate": "abc"}, 369930.0)[0], 0.0)
check("no amount deducts nothing",
      _tds_for({}, WITH_GST, {"tds_rate": 0.1}, None)[0], 0.0)
check("2% rounds to the rupee",
      _tds_for({}, {"subtotal": 10163.34}, {"tds_rate": 0.02}, 10163.34)[0], 203.0)

print("\nTDS the vendor has already deducted on the invoice")
# Prodbrew bills the net: 420000 + 75600 GST - 8400 TDS = 487200. Deducting
# again would pay them 478800 and short-change them by the full amount.
PRODBREW_1002 = {"invoice_number": "PF-2026-OCT-1002", "subtotal": 420000,
                 "igst": 75600, "tax_total": 75600, "total_amount": 487200}
PRODBREW_1001 = {"invoice_number": "PF-2026-OCT-1001", "subtotal": 230000,
                 "igst": 41400, "tax_total": 41400, "total_amount": 266800}

check("1002 deducts nothing further",
      _tds_for({}, PRODBREW_1002, {"tds_rate": 0.02}, 487200.0)[0], 0.0)
check("and says why", _tds_for({}, PRODBREW_1002, {"tds_rate": 0.02}, 487200.0)[1],
      "already deducted on the invoice")
check("1001 deducts nothing further",
      _tds_for({}, PRODBREW_1001, {"tds_rate": 0.02}, 266800.0)[0], 0.0)

# Xperforce bills the gross: 225000 + 40500 = 265500, nothing withheld, so the
# 10% still has to come off.
XPERFORCE = {"invoice_number": "2026-27/D-06", "subtotal": 225000,
             "cgst": 20250, "sgst": 20250, "igst": 40500,
             "tax_total": 40500, "total_amount": 265500}
check("a gross invoice still has TDS deducted",
      _tds_for({}, XPERFORCE, {"tds_rate": 0.1}, 265500.0)[0], 22500.0)

# A discount also makes the total smaller than subtotal + tax. It must not be
# mistaken for TDS: Innovatecture's rental takes 10% off, nothing like 2%.
DISCOUNTED = {"subtotal": 187000, "cgst": 16830, "sgst": 16830,
              "tax_total": 33660, "total_amount": 201960}
check("a discount is not mistaken for TDS",
      _tds_for({}, DISCOUNTED, {"tds_rate": 0.02}, 201960.0)[0], 3740.0)

print("\nReading TDS rates from the master sheet")
from sync_vendor_tds import parse_rate  # noqa: E402

check("blank is zero", parse_rate(None), (0.0, None))
check("empty string is zero", parse_rate(""), (0.0, None))
check("a dash is zero", parse_rate("-"), (0.0, None))
check("0.1 stays 0.1", parse_rate(0.1), (0.1, None))
check("10 means 10 per cent", parse_rate(10), (0.1, None))
check("'10%' means 10 per cent", parse_rate("10%"), (0.1, None))
check("2 means 2 per cent", parse_rate(2), (0.02, None))
check("text is a problem, not a zero", parse_rate("abc")[0], None)
check("negative is a problem", parse_rate(-1)[0], None)
check("80 is not a TDS rate", parse_rate(80)[0], None)
check("1 is ambiguous and refused", parse_rate(1)[0], None)

print("\n" + "=" * 58)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
