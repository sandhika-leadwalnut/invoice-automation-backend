"""
Undo the damage from our own GSTIN being stored as a vendor's.

Extraction sometimes reads the BUYER's GSTIN into vendor_gstin. That alone is
harmless - _usable_vendor_gstin refuses it. The damage comes from a vendor
record in invoice_db.vendors that also carries our GSTIN: every such invoice
matched it, and was handed that vendor's identity, credit period and bank
account.

INNOVATECTURE DESIGN SERVICES LLP is the record this happened with. A Royal
Lotus View rental bill was booked to INNOVATECTURE in Zoho because of it.

The guard stops it happening again, but it is consulted only when the invoice
has no vendor_id - and invoices ingested before the guard already have the
wrong one stamped on them, so Accept still sends them to the wrong vendor.
This finds both.

    python fix_company_gstin_matches.py
    python fix_company_gstin_matches.py --apply
"""
import argparse

from pymongo import MongoClient

from config import settings


def norm(value):
    return "".join(str(value or "").upper().split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    ours = norm(settings.company_gstin)
    print(f"Our own GSTIN: {settings.company_gstin}\n")

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    db = client["invoice_db"]
    vendors, invoices = db["vendors"], db["invoices"]

    # 1. Vendor records carrying our GSTIN -------------------------------------
    bad_vendors = [v for v in vendors.find({}) if norm(v.get("gstin")) == ours]

    if bad_vendors:
        print(f"VENDOR RECORDS HOLDING OUR GSTIN ({len(bad_vendors)}):")
        for v in bad_vendors:
            print(f"   {v.get('vendor_name')}   vendor_id={v.get('vendor_id')}")
        print("\n   Clearing it. Their real GSTIN, if they have one, has to come")
        print("   from the vendor master - it cannot be worked out from here.")
    else:
        print("No vendor record holds our GSTIN.")

    # 2. Invoices stamped with one of those vendors ----------------------------
    bad_ids = {v.get("vendor_id") for v in bad_vendors if v.get("vendor_id")}

    stamped = []
    if bad_ids:
        stamped = list(invoices.find({
            "deleted_at": {"$exists": False},
            "$or": [
                {"vendor_id": {"$in": list(bad_ids)}},
                {"invoice_data.vendor_id": {"$in": list(bad_ids)}},
            ],
        }, {"vendor_name": 1, "invoice_number": 1, "status": 1,
            "vendor_id": 1, "vendor_gstin": 1, "invoice_data.vendor_name": 1}))

    pending, already_pushed = [], []
    for doc in stamped:
        (already_pushed if doc.get("status") in ("accepted", "paid") else pending).append(doc)

    if pending:
        print(f"\nSTILL PENDING ({len(pending)}) - vendor_id will be cleared so they")
        print("re-resolve on Accept:")
        for doc in pending:
            data = doc.get("invoice_data") or {}
            name = doc.get("vendor_name") or data.get("vendor_name") or "?"
            print(f"   {str(doc['_id'])[:8]}  {name[:30]:32} {doc.get('invoice_number') or '?'}")

    if already_pushed:
        print(f"\nALREADY IN ZOHO ({len(already_pushed)}) - NOT touched here:")
        for doc in already_pushed:
            data = doc.get("invoice_data") or {}
            name = doc.get("vendor_name") or data.get("vendor_name") or "?"
            print(f"   {str(doc['_id'])[:8]}  {name[:30]:32} {doc.get('invoice_number') or '?'}"
                  f"  [{doc.get('status')}]")
        print("\n   These are booked against the wrong vendor IN ZOHO. Clearing")
        print("   anything here would not change that. Each one has to be")
        print("   corrected in Zoho by Finance - this script will not pretend")
        print("   to have fixed the books.")

    # 3. Invoices carrying our GSTIN as the vendor's ---------------------------
    carrying = [
        d for d in invoices.find(
            {"deleted_at": {"$exists": False}},
            {"vendor_name": 1, "vendor_gstin": 1, "invoice_number": 1, "status": 1,
             "invoice_data.vendor_gstin": 1, "invoice_data.vendor_name": 1})
        if ours in (norm(d.get("vendor_gstin")),
                    norm((d.get("invoice_data") or {}).get("vendor_gstin")))
    ]
    if carrying:
        print(f"\nINVOICES WHOSE VENDOR GSTIN IS OURS ({len(carrying)}):")
        for doc in carrying:
            data = doc.get("invoice_data") or {}
            name = doc.get("vendor_name") or data.get("vendor_name") or "?"
            print(f"   {str(doc['_id'])[:8]}  {name[:30]:32} {doc.get('invoice_number') or '?'}"
                  f"  [{doc.get('status')}]")
        print("\n   Left as they are: the GSTIN on the invoice is what the vendor")
        print("   actually sent, and rewriting it would hide a bad invoice rather")
        print("   than fix it. The guard ignores it for matching already.")

    print("\n" + "-" * 62)
    print(f"{len(bad_vendors)} vendor record(s), {len(pending)} pending invoice(s) to clear, "
          f"{len(already_pushed)} already in Zoho")

    if not args.apply:
        print("\nDry run. Nothing written.")
        return

    for v in bad_vendors:
        vendors.update_one({"vendor_id": v["vendor_id"]}, {"$unset": {"gstin": ""}})

    for doc in pending:
        invoices.update_one(
            {"_id": doc["_id"]},
            {"$unset": {"vendor_id": "", "zoho_contact_id": "",
                        "invoice_data.vendor_id": "", "invoice_data.zoho_contact_id": ""}},
        )

    print(f"\nCleared the GSTIN on {len(bad_vendors)} vendor record(s) and the stamped")
    print(f"vendor on {len(pending)} pending invoice(s).")
    print("\nNext: put the real GSTIN for those vendors into the vendor master,")
    print("and ask Finance to correct the bills already in Zoho.")


if __name__ == "__main__":
    main()
