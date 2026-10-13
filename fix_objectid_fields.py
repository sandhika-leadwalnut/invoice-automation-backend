"""
Convert stray ObjectId values inside invoice documents to strings.

bank_check.matched_vendor.id was written with the vendor's raw _id. The vendors
collection keys on ObjectId while invoices key on a uuid string, so those
documents could not be serialised to JSON - and because /verification/invoices/all
returns every invoice in one response, a single bad document returned a 500 and
blanked the whole dashboard.

The fix in verification_router stops new ones being written and makes the
endpoint tolerant. This repairs what is already stored.

    python fix_objectid_fields.py          # dry run
    python fix_objectid_fields.py --apply
"""
import argparse

from bson import ObjectId
from pymongo import MongoClient, UpdateOne

from config import settings

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="write the changes")
args = parser.parse_args()

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
invoices = client["invoice_db"]["invoices"]


def convert(value):
    """Return (new_value, changed)."""
    if isinstance(value, ObjectId):
        return str(value), True
    if isinstance(value, dict):
        changed = False
        out = {}
        for key, inner in value.items():
            out[key], hit = convert(inner)
            changed = changed or hit
        return out, changed
    if isinstance(value, list):
        changed = False
        out = []
        for inner in value:
            new, hit = convert(inner)
            out.append(new)
            changed = changed or hit
        return out, changed
    return value, False


operations = []
for doc in invoices.find({}):
    # The _id of the invoice itself is left alone. Rewriting a primary key
    # would orphan every reference to it; only the fields are repaired.
    fields = {k: v for k, v in doc.items() if k != "_id"}
    fixed, changed = convert(fields)
    if not changed:
        continue
    operations.append(UpdateOne({"_id": doc["_id"]}, {"$set": fixed}))
    print(f"  {doc['_id']}   {doc.get('vendor_name') or '?'}")

print()
if not operations:
    print("Nothing to fix - no ObjectId values found inside any invoice.")
    raise SystemExit(0)

print(f"{len(operations)} document(s) carry an ObjectId in a field.")

# Invoices whose _id is itself an ObjectId were inserted by hand rather than by
# the service, which always uses a uuid string. Worth knowing about: they will
# not match any lookup the service does by id.
strays = invoices.count_documents({"_id": {"$type": "objectId"}})
if strays:
    print(f"\n!! {strays} invoice(s) have an ObjectId as their _id, so they were")
    print("   not created by the service. This script does not touch those -")
    print("   check whether they were added by hand and should be there.")

if not args.apply:
    print("\nDry run. Nothing written. Re-run with --apply.")
    raise SystemExit(0)

result = invoices.bulk_write(operations)
print(f"\nUpdated {result.modified_count} document(s).")
