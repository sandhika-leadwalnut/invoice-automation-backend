"""
Re-run the vendor bank check against invoices already in the database.

bank_check is written once, at ingestion, and never recomputed. That is normally
right - it records what was true when the invoice arrived. But it goes stale in
two ways that matter:

  - a vendor had no bank details on file at the time and has them now, so a
    correct invoice still carries "Bank not on file";
  - the comparison itself was improved, so an invoice flagged by the old,
    stricter version now matches. The OCR correction for IFSC codes misread
    with a letter O in place of the reserved zero is the case in point.

Both leave red badges on invoices that are fine, and a warning people learn to
ignore is worse than no warning at all.

Only pending invoices are touched by default. An accepted or paid invoice has
already been through a person and into Zoho; rewriting its flag afterwards would
change the record of what was seen at the time it was approved.

    python recheck_bank_details.py                  # dry run, everything pending
    python recheck_bank_details.py --apply
    python recheck_bank_details.py --vendor "Veave" --apply
    python recheck_bank_details.py --id d3c6cf8c-... --apply
"""
import argparse
import asyncio
import re
import sys

from verification_router import (
    NOT_DELETED, _check_bank_details, _find_vendor_doc, invoices_col,
)

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="write the new results")
parser.add_argument("--vendor", default=None, help="limit to vendors matching this name")
parser.add_argument("--id", default=None, help="a single invoice id")
parser.add_argument("--include-accepted", action="store_true",
                    help="also re-check accepted and paid invoices (records what "
                         "is true now, not what the approver saw)")
args = parser.parse_args()

ARROW = {
    ("mismatch", "matched"): "cleared - was a false alarm",
    ("unverified", "matched"): "now verified against the vendor master",
    ("unverified", "mismatch"): "NOW A MISMATCH - look at this one",
    ("matched", "mismatch"): "NOW A MISMATCH - look at this one",
    ("matched", "unverified"): "lost its vendor bank details",
    ("mismatch", "unverified"): "lost its vendor bank details",
}


async def main():
    query = dict(NOT_DELETED)
    if args.id:
        query["_id"] = args.id
    if args.vendor:
        query["vendor_name"] = re.compile(re.escape(args.vendor), re.IGNORECASE)
    if not args.include_accepted:
        query["status"] = {"$nin": ["accepted", "paid"]}

    cursor = invoices_col.find(query)
    docs = await cursor.to_list(length=5000)

    if not docs:
        print("No invoices matched.")
        return

    print(f"Re-checking {len(docs)} invoice(s).\n")

    changes, unchanged, skipped = [], 0, 0

    for doc in docs:
        # The same payload the check saw at ingestion: the accepted version if a
        # person edited one, otherwise what was extracted.
        payload = doc.get("accepted_data") or doc.get("edited_data") or doc.get("invoice_data")
        if not isinstance(payload, dict):
            skipped += 1
            continue

        vendor = await _find_vendor_doc(payload)
        fresh = await _check_bank_details(payload, vendor)

        before = (doc.get("bank_check") or {}).get("status")
        after = fresh["status"] if fresh else None

        if before == after:
            unchanged += 1
            continue

        note = ARROW.get((before, after), "")
        changes.append((doc, fresh, before, after, note))

        print(f"  {doc.get('vendor_name') or '?'}")
        print(f"    invoice {payload.get('invoice_number') or '?'}   id {doc['_id']}")
        print(f"    {before or 'no check'}  ->  {after or 'no check'}   {note}")
        if fresh and fresh.get("ocr_corrected"):
            print(f"    matched after correcting: {', '.join(fresh['ocr_corrected'])}")
        if fresh and fresh.get("differences"):
            for d in fresh["differences"]:
                print(f"    {d['field']}: invoice {d['on_invoice']!r} vs record {d['on_record']!r}")
        print()

    print("-" * 60)
    print(f"{len(changes)} would change, {unchanged} unchanged, {skipped} had no payload to re-check")

    worse = [c for c in changes if c[3] == "mismatch"]
    if worse:
        print(f"\n!! {len(worse)} invoice(s) become a MISMATCH. Read those before applying.")

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply.")
        return

    for doc, fresh, _, _, _ in changes:
        if fresh is None:
            await invoices_col.update_one({"_id": doc["_id"]}, {"$unset": {"bank_check": ""}})
        else:
            await invoices_col.update_one({"_id": doc["_id"]}, {"$set": {"bank_check": fresh}})

    print(f"\nUpdated {len(changes)} invoice(s).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
