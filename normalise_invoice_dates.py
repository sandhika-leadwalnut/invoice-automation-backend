"""
Rewrite stored invoice dates to a plain YYYY-MM-DD.

fix_flipped_dates.py corrected the US/UK date flips but wrote datetimes back,
so the affected invoices carry "2026-10-09T00:00:00" where every other record
has "2026-10-09". Two consequences:

  - Zoho rejects the bill on Accept with "Invalid value passed for Invoice
    Date", which reads like the date is wrong rather than its format.
  - the review form shows the time as well, which invites someone to tidy it
    by hand and get the day wrong.

zoho_client now normalises on the way out, so the push works regardless. This
tidies what is stored, so the two stop disagreeing.

Only the format changes. A date that cannot be read is reported and left
alone - rewriting one this script does not understand is how a day gets lost.

    python normalise_invoice_dates.py
    python normalise_invoice_dates.py --apply
"""
import argparse
from datetime import datetime

from pymongo import MongoClient, UpdateOne

from config import settings
from zoho_client import as_zoho_date

# Where a date can be hiding. The top-level copy is what ingestion promotes;
# the nested ones are what the reviewer sees and what is sent to Zoho.
DATE_PATHS = (
    "invoice_date",
    "invoice_data.invoice_date",
    "edited_data.invoice_date",
    "accepted_data.invoice_date",
)


def read_path(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    invoices = client["invoice_db"]["invoices"]

    operations, unreadable = [], []
    changed_docs = 0

    for doc in invoices.find({}, {p.split(".")[0]: 1 for p in DATE_PATHS} | {"vendor_name": 1}):
        updates = {}
        for path in DATE_PATHS:
            value = read_path(doc, path)
            if value in (None, ""):
                continue

            normalised = as_zoho_date(value)
            if normalised == value:
                continue

            # as_zoho_date hands back the original when it cannot read it.
            if not isinstance(normalised, str) or len(normalised) != 10:
                unreadable.append((doc["_id"], path, value))
                continue

            updates[path] = normalised

        if not updates:
            continue

        changed_docs += 1
        operations.append(UpdateOne({"_id": doc["_id"]}, {"$set": updates}))
        name = doc.get("vendor_name") or "?"
        for path, new in updates.items():
            old = read_path(doc, path)
            print(f"   {str(doc['_id'])[:8]}  {name[:26]:28} {path:28} {old!r} -> {new!r}")

    print()
    if unreadable:
        print(f"COULD NOT READ ({len(unreadable)}) - left exactly as they are:")
        for _id, path, value in unreadable:
            print(f"   {str(_id)[:8]}  {path}  {value!r}")
        print()

    print("-" * 60)
    print(f"{changed_docs} invoice(s) would change, {len(unreadable)} unreadable")

    if not args.apply:
        print("\nDry run. Nothing written.")
        return
    if not operations:
        print("\nNothing to do.")
        return

    result = invoices.bulk_write(operations)
    print(f"\nUpdated {result.modified_count} invoice(s).")


if __name__ == "__main__":
    main()
