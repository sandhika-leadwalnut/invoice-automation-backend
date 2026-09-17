"""
Replay test for the duplicate-detection helpers.

Exercises the real functions in verification_router, not a copy of them, so the
test fails if the implementation drifts. No Mongo required - every function
under test is pure.
"""
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verification_router import (  # noqa: E402
    _norm_invoice_number,
    _financial_year,
    _vendor_key,
    _dedupe_key,
    _identity_fingerprint,
    _file_sha256,
    _parse_invoice_date,
)

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}")
        print(f"        got      {actual!r}")
        failures.append(label)


def same(label, a, b):
    ok = a == b and a is not None
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        {a!r}\n        {b!r}")
        failures.append(label)


def differ(label, a, b):
    ok = a != b
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        both were {a!r}")
        failures.append(label)


# A real record from the database, trimmed to the fields that matter.
BASE = {
    "invoice_number": "LeadWalnut - 6",
    "invoice_date": "2026-09-10",
    "vendor_name": "BATTALION COMMERCE",
    "vendor_gstin": "29DMNPB5877C1ZQ",
    "vendor_id": "7f01a931-23ba-474c-b471-0a5e70ddedcd",
    "total_amount": 70800,
}


def variant(**overrides):
    d = dict(BASE)
    d.update(overrides)
    return d


print("\n1. Invoice number normalisation")
check("spacing variance collapses",
      _norm_invoice_number("LeadWalnut - 6"), _norm_invoice_number("LeadWalnut-6"))
check("case folds", _norm_invoice_number("inv-001"), "INV-001")
differ("separate series stay separate (Rule 46(b) allows both)",
       _norm_invoice_number("INV/001"), _norm_invoice_number("INV-001"))

print("\n2. Financial year (April-March)")
check("September 2026 -> 2026-2027", _financial_year("2026-09-10"), "2026-2027")
check("March 2026 -> 2025-2026", _financial_year("2026-03-31"), "2025-2026")
check("April 1 flips the year", _financial_year("2026-04-01"), "2026-2027")
check("dd/mm/yyyy parses", _financial_year("10/09/2026"), "2026-2027")
check("unparseable date is explicit", _financial_year("not a date"), "unknown")
check("dd-mm-yyyy is read the Indian way",
      str(_parse_invoice_date("03-04-2026")), "2026-04-03")

print("\n3. Vendor key ladder")
check("vendor_id wins when present",
      _vendor_key(BASE), "vendor_id:7F01A931-23BA-474C-B471-0A5E70DDEDCD")
check("falls back to GSTIN when unmapped",
      _vendor_key({"vendor_gstin": "29DMNPB5877C1ZQ", "vendor_name": "X"}),
      "vendor_gstin:29DMNPB5877C1ZQ")
check("GSTIN-less mapped vendor still keys on vendor_id",
      _vendor_key({"vendor_id": "abc", "vendor_name": "Authority Builders LLC"}),
      "vendor_id:ABC")
check("name only as last resort",
      _vendor_key({"vendor_name": "TrioSEO"}), "vendor_name:TRIOSEO")
check("nothing identifiable yields nothing", _vendor_key({}), "")

print("\n4. The real extraction-drift case (INV-000698)")
elpro = {"invoice_number": "INV-000698", "invoice_date": "2026-09-10",
         "vendor_id": "fye-123", "vendor_name": "ElproDigital by FYE Digit Informatics LLP",
         "total_amount": 5000}
fye = {"invoice_number": "INV-000698", "invoice_date": "2026-09-10",
       "vendor_id": "fye-123", "vendor_name": "FYE Digit Informatics LLP",
       "total_amount": 5000}
same("vendor_id anchors through inconsistent name extraction",
     _identity_fingerprint(elpro), _identity_fingerprint(fye))

print("\n5. The four-field match matrix (auto-drop needs ALL four)")
same("all four identical -> same fingerprint",
     _identity_fingerprint(BASE), _identity_fingerprint(variant()))
differ("different amount", _identity_fingerprint(BASE),
       _identity_fingerprint(variant(total_amount=70801)))
differ("different date", _identity_fingerprint(BASE),
       _identity_fingerprint(variant(invoice_date="2026-09-11")))
differ("different invoice number", _identity_fingerprint(BASE),
       _identity_fingerprint(variant(invoice_number="LeadWalnut - 7")))
differ("different vendor", _identity_fingerprint(BASE),
       _identity_fingerprint(variant(vendor_id="someone-else")))
differ("same number, next financial year", _identity_fingerprint(BASE),
       _identity_fingerprint(variant(invoice_date="2026-03-10")))
differ("two vendors reusing INV-001 do NOT collide",
       _identity_fingerprint({"invoice_number": "INV-001", "invoice_date": "2026-05-01",
                              "vendor_id": "vendor-a", "total_amount": 100}),
       _identity_fingerprint({"invoice_number": "INV-001", "invoice_date": "2026-05-01",
                              "vendor_id": "vendor-b", "total_amount": 100}))

print("\n6. Tray path: same bill family, details differ")
same("corrected invoice shares the dedupe key",
     _dedupe_key(BASE), _dedupe_key(variant(total_amount=99999)))
differ("...but not the fingerprint, so it is flagged not dropped",
       _identity_fingerprint(BASE), _identity_fingerprint(variant(total_amount=99999)))

print("\n7. Missing data can never cause an auto-drop")
check("no amount -> no fingerprint",
      _identity_fingerprint(variant(total_amount=None)), None)
check("no date -> no fingerprint",
      _identity_fingerprint(variant(invoice_date=None)), None)
check("no invoice number -> no key",
      _dedupe_key(variant(invoice_number=None)), None)

print("\n8. File fingerprint")
import base64 as _b64  # noqa: E402
pdf_a = _b64.b64encode(b"%PDF-1.4 fake invoice bytes").decode()
pdf_b = _b64.b64encode(b"%PDF-1.4 fake invoice bytes").decode()
pdf_c = _b64.b64encode(b"%PDF-1.4 a different file").decode()
same("identical bytes -> identical hash", _file_sha256(pdf_a), _file_sha256(pdf_b))
differ("different bytes -> different hash", _file_sha256(pdf_a), _file_sha256(pdf_c))
check("no file -> no hash", _file_sha256(None), None)
check("corrupt base64 degrades quietly", _file_sha256("!!!not base64!!!"), None)

print("\n" + "=" * 58)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
