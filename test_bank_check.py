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

from verification_router import _check_bank_details, _norm_account  # noqa: E402

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

print("\n" + "=" * 58)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
