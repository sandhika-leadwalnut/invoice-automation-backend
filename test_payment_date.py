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

print("\n" + "=" * 58)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
