"""
Replay the payment sheet's vendor lookup against live data, step by step.

Answers "why does this invoice say no bank details" without guessing: it walks
the same ladder the endpoint walks and reports which rung fails and why.

    python diagnose_payment_sheet.py "Mediavak India Private Limited"
    python diagnose_payment_sheet.py "Mediavak India Private Limited" "S. Parween"
"""
import sys

from pymongo import MongoClient

from config import settings
from verification_router import _norm_vendor_name

OK, BAD, INFO = "  [OK]  ", "  [FAIL]", "  [--]  "

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
db = client["invoice_db"]
vendors, invoices = db["vendors"], db["invoices"]

print(f"vendors: {vendors.count_documents({})}   "
      f"with bank_details: {vendors.count_documents({'bank_details': {'$exists': True}})}   "
      f"with aliases: {vendors.count_documents({'aliases': {'$exists': True}})}")

for wanted in sys.argv[1:]:
    print("\n" + "=" * 70)
    print(f"INVOICE VENDOR NAME: {wanted!r}")
    print("=" * 70)

    target = _norm_vendor_name(wanted)
    print(f"  normalises to: {target}")

    # --- the invoices carrying this name ---
    docs = list(invoices.find(
        {"vendor_name": wanted, "deleted_at": {"$exists": False}},
        {"vendor_id": 1, "vendor_gstin": 1, "status": 1, "invoice_data": 1, "accepted_data": 1},
    ).limit(5))
    print(f"\n--- invoices with this vendor_name: {invoices.count_documents({'vendor_name': wanted})} ---")
    for d in docs:
        data = d.get("accepted_data") or d.get("invoice_data") or {}
        vid = d.get("vendor_id") or data.get("vendor_id")
        gst = d.get("vendor_gstin") or data.get("vendor_gstin")
        print(f"   {str(d['_id'])[:8]}  status={d.get('status'):10} "
              f"vendor_id={vid or '-'}  gstin={gst or '-'}")
        if vid:
            print(f"{OK}resolves by vendor_id - should never reach the name step")

    # --- step 3: the name match ---
    print(f"\n--- name match (only used when vendor_id and gstin are absent) ---")
    candidates = list(vendors.find(
        {"bank_details": {"$exists": True}},
        {"vendor_name": 1, "aliases": 1, "bank_details": 1, "vendor_id": 1},
    ))
    print(f"{INFO}{len(candidates)} vendor(s) have bank_details and are eligible")

    hits = []
    for v in candidates:
        names = [v.get("vendor_name")] + list(v.get("aliases") or [])
        if any(_norm_vendor_name(n) == target for n in names):
            hits.append(v)

    if len(hits) == 1:
        v = hits[0]
        print(f"{OK}matched {v.get('vendor_name')!r}")
        print(f"       bank: {v.get('bank_details')}")
        continue
    if len(hits) > 1:
        print(f"{BAD}{len(hits)} vendors match - ambiguous, so nothing is chosen:")
        for v in hits:
            print(f"        {v.get('vendor_name')}")
        continue

    print(f"{BAD}no vendor matched")

    # --- why not ---
    near = list(vendors.find(
        {"$or": [
            {"vendor_name": {"$regex": wanted.split()[0], "$options": "i"}},
            {"aliases": {"$regex": wanted.split()[0], "$options": "i"}},
        ]},
        {"vendor_name": 1, "aliases": 1, "bank_details": 1},
    ))
    if not near:
        print(f"{INFO}no vendor has a similar name at all - it may not exist yet")
    for v in near:
        name = v.get("vendor_name")
        has_bank = "bank_details" in v
        aliases = v.get("aliases") or []
        print(f"\n   candidate: {name!r}")
        print(f"      bank_details : {'yes' if has_bank else 'NO - excluded from the lookup entirely'}")
        print(f"      aliases      : {aliases or 'none'}")
        print(f"      name norm    : {_norm_vendor_name(name)}  "
              f"{'== target' if _norm_vendor_name(name) == target else '!= target'}")
        for a in aliases:
            print(f"      alias norm   : {_norm_vendor_name(a)}  "
                  f"{'== target' if _norm_vendor_name(a) == target else '!= target'}")
        if not has_bank:
            print("      >>> FIX: this vendor has no bank_details, so it is never a candidate.")
            print("               Run update_vendors.py --apply")
        elif not aliases:
            print("      >>> FIX: no aliases recorded. Run:")
            print("               python suggest_vendor_aliases.py")
            print("               python suggest_vendor_aliases.py --apply alias_map.csv")
