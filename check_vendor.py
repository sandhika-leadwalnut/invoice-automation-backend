"""
Verify a vendor is correctly inserted in invoice_db.vendors.

Read-only. Does not modify Mongo, Zoho, or any file.

It checks more than "does a document exist" - it replays the three DB lookups
that resolve_vendor_zoho_contact() in verification_router.py actually performs,
so you find out whether real invoices will resolve, not just whether a row is
sitting there.

Usage:
    python check_vendor.py "Xperforce Technologies LLP"
    python check_vendor.py "Xperforce Technologies LLP" --gstin 29AAAFX1958J2ZO
"""

import argparse
import asyncio
import re

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings

REQUIRED_STR_FIELDS = ["vendor_id", "vendor_name", "ledger_id", "zoho_contact_id"]

OK = "  [OK]  "
BAD = "  [FAIL]"
WARN = "  [WARN]"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendor_name")
    ap.add_argument("--gstin", default=None)
    args = ap.parse_args()

    client = AsyncIOMotorClient(settings.mongo_uri)
    vendors = client["invoice_db"]["vendors"]

    print(f"\nCollection invoice_db.vendors -> {await vendors.count_documents({})} documents total")

    # --- 1. exact-name lookup, mirroring the resolver's case-insensitive regex ---
    name_regex = re.compile(f"^{re.escape(args.vendor_name.strip())}$", re.IGNORECASE)
    docs = await vendors.find({"vendor_name": name_regex}).to_list(length=10)

    print(f"\n--- Lookup by vendor_name ({args.vendor_name!r}) ---")
    if not docs:
        print(f"{BAD} no document found. The insert did not land, or the name differs.")
        near = await vendors.find(
            {"vendor_name": re.compile(re.escape(args.vendor_name.strip()[:6]), re.IGNORECASE)}
        ).to_list(length=5)
        if near:
            print("        similar names present:")
            for d in near:
                print(f"          - {d.get('vendor_name')!r}")
        client.close()
        raise SystemExit(1)

    if len(docs) > 1:
        print(f"{WARN} {len(docs)} documents share this name - duplicates will make resolution "
              f"non-deterministic. Delete the extras.")

    doc = docs[0]
    print(f"{OK} found (_id {doc.get('_id')})")

    # --- 2. field presence and, critically, type ---
    print("\n--- Field check ---")
    ok = True
    for f in REQUIRED_STR_FIELDS:
        v = doc.get(f)
        if v is None or v == "":
            print(f"{BAD} {f}: missing")
            ok = False
        elif not isinstance(v, str):
            # a 19-digit id stored as a number loses precision and breaks exact-match queries
            print(f"{BAD} {f}: {v!r} is {type(v).__name__}, expected str")
            ok = False
        else:
            print(f"{OK} {f}: {v!r}")

    g = doc.get("gstin")
    if g is None:
        print(f"{WARN} gstin: null (fine only if this vendor genuinely has no GSTIN)")
    elif not isinstance(g, str):
        print(f"{BAD} gstin: {g!r} is {type(g).__name__}, expected str")
        ok = False
    else:
        print(f"{OK} gstin: {g!r}")

    for f in ("zoho_contact_id", "ledger_id"):
        v = doc.get(f)
        if isinstance(v, str) and not v.isdigit():
            print(f"{BAD} {f}: {v!r} is not all digits - looks corrupted (e.g. '5.58938E+18')")
            ok = False

    # --- 3. replay the resolver's own lookups ---
    print("\n--- Resolver simulation (as verification_router does it) ---")
    checks = [
        ("by vendor_id", {"vendor_id": doc.get("vendor_id")}),
        ("by vendor_name", {"vendor_name": name_regex}),
    ]
    gstin = args.gstin or doc.get("gstin")
    if gstin:
        checks.insert(1, ("by gstin", {"gstin": gstin.strip()}))

    for label, q in checks:
        hit = await vendors.find_one(q)
        if hit and hit.get("zoho_contact_id"):
            print(f"{OK} {label}: resolves -> zoho_contact_id {hit.get('zoho_contact_id')}")
        else:
            print(f"{BAD} {label}: does NOT resolve")
            ok = False

    print("\n" + ("PASS - invoices for this vendor will resolve." if ok
                  else "FAIL - see the [FAIL] lines above."))
    client.close()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
