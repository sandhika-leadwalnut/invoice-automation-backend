"""
Tests for the vendor bank detail check.

Runs against the real functions in verification_router. The async wrapper is
driven directly so no database is needed - the vendor record is passed in.

    python test_bank_check.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verification_router import (  # noqa: E402
    _check_bank_details, _norm_account, _norm_ifsc, _accounts_match,
)

failures = []
VENDOR = {"bank_details": {"bank": "HDFC Bank", "ifsc": "HDFC0001388",
                           "account_number": "50100723881009"}}


def check(label, actual, expected):
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


def status_of(payload, vendor=VENDOR):
    result = asyncio.run(_check_bank_details(payload, vendor))
    return result["status"] if result else None


print("\n1. Nothing on the invoice stays silent")
check("no bank fields at all", status_of({"invoice_number": "1"}), None)
check("empty strings", status_of({"bank_account": "", "ifsc": ""}), None)
check("a dash", status_of({"bank_account": "-"}), None)

print("\n2. Matching details")
check("exact", status_of({"bank_account": "50100723881009", "ifsc": "HDFC0001388"}), "matched")
check("spaces in the account number",
      status_of({"bank_account": "5010 0723 8810 09", "ifsc": "HDFC0001388"}), "matched")
check("lower-case IFSC",
      status_of({"bank_account": "50100723881009", "ifsc": "hdfc0001388"}), "matched")
check("hyphens", status_of({"bank_account": "50100-723881009"}), "matched")
check("account only, no IFSC given", status_of({"bank_account": "50100723881009"}), "matched")

print("\n3. Genuine changes are caught")
check("different account", status_of({"bank_account": "99999999999999"}), "mismatch")
check("different IFSC", status_of({"ifsc": "ICIC0000297"}), "mismatch")
check("both different",
      status_of({"bank_account": "99999999999999", "ifsc": "ICIC0000297"}), "mismatch")
check("one digit different - the dangerous case",
      status_of({"bank_account": "50100723881008"}), "mismatch")

print("\n4. Alternative field names from extraction")
for field in ("vendor_bank_account", "account_number", "bank_account_number", "account_no"):
    check(f"account via {field!r}", status_of({field: "99999999999999"}), "mismatch")
for field in ("vendor_bank_ifsc", "ifsc_code", "bank_ifsc"):
    check(f"ifsc via {field!r}", status_of({field: "ICIC0000297"}), "mismatch")

print("\n5. Vendor with nothing on record")
check("flagged unverified, not a mismatch",
      status_of({"bank_account": "50100723881009"}, {"bank_details": {}}), "unverified")
check("no vendor at all",
      status_of({"bank_account": "50100723881009"}, None), "unverified")

print("\n6. What a mismatch reports")
result = asyncio.run(_check_bank_details(
    {"bank_account": "99999999999999", "ifsc": "ICIC0000297",
     "vendor_name": "ACME", "invoice_number": "7"}, VENDOR))
check("names both changed fields", len(result["differences"]), 2)
check("keeps the account on record",
      result["differences"][0]["on_record"], "50100723881009")
check("keeps the account on the invoice",
      result["differences"][0]["on_invoice"], "99999999999999")

print("\n7. Normalisation")
check("punctuation ignored", _norm_account("5010-0723 8810/09"), "50100723881009")
check("case folded", _norm_account("hdfc0001388"), "HDFC0001388")
check("None is empty", _norm_account(None), "")

print("\n8. OCR misreads letters as digits")
# The real case: Meeting Minds Infosystems, invoice 14-2026-27. The PDF says
# KARB0000107; extraction returned KARBO000107, with a letter O in the
# position the RBI reserves for a zero. The account number was read correctly.
MEETING_MINDS = {"vendor_name": "MEETING MINDS INFOSYSTEMS",
                 "bank_details": {"bank": "Karnataka Bank", "ifsc": "KARB0000107",
                                  "account_number": "1072000110077201"}}
check("letter O for zero in the reserved IFSC position",
      status_of({"vendor_bank_account": "1072000110077201",
                 "vendor_bank_ifsc": "KARBO000107"}, MEETING_MINDS), "matched")
check("the correction is recorded, not hidden",
      asyncio.run(_check_bank_details(
          {"vendor_bank_ifsc": "KARBO000107"}, MEETING_MINDS))["ocr_corrected"], ["ifsc"])
check("a clean match records no correction",
      asyncio.run(_check_bank_details(
          {"vendor_bank_ifsc": "KARB0000107"}, MEETING_MINDS))["ocr_corrected"], None)
check("misread digits in a numeric account",
      status_of({"bank_account": "5O1OO723881OO9"}), "matched")
check("letters in the last six of an IFSC are left alone",
      _norm_ifsc("KARB0OOO107"), "KARB0OOO107")

print("\n9. OCR folding must not hide a real change")
check("a genuinely different account still fails",
      status_of({"bank_account": "99999999999999"}), "mismatch")
check("one digit different still fails",
      status_of({"bank_account": "50100723881008"}), "mismatch")
check("a different bank's IFSC still fails",
      status_of({"ifsc": "ICIC0000297"}), "mismatch")
check("same digits, different length is not a match",
      _accounts_match("5010072388100", "50100723881009"), False)
check("folding is refused when the record is not numeric",
      _accounts_match("OO123", "00123X"), False)

print("\n10. Account numbers typed as numbers by extraction")
check("a float does not become scientific notation",
      status_of({"vendor_bank_account": 1072000110077201.0}, MEETING_MINDS), "matched")
check("leading zeros survive when sent as text",
      status_of({"vendor_bank_account": "000905026841"},
                {"bank_details": {"account_number": "000905026841"}}), "matched")

print("\n11. The flag says which vendor it compared against")
res = asyncio.run(_check_bank_details(
    {"bank_account": "99999999999999"}, MEETING_MINDS))
check("names the vendor record used",
      res["matched_vendor"]["name"], "MEETING MINDS INFOSYSTEMS")

print("\n" + "=" * 58)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
