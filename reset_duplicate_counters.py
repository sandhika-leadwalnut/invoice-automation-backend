"""
Reset the duplicate counters back to zero.

Local testing writes real rows into the production Atlas database: every blocked
duplicate adds a row to duplicate_metrics and bumps duplicate_hits on the
invoice it matched. This clears both so Finance starts from a clean slate.

It does NOT touch invoices themselves - remove stray test invoices from the
dashboard with bulk delete, which soft-deletes and is recoverable.

    python reset_duplicate_counters.py            # show what would change
    python reset_duplicate_counters.py --apply    # actually change it
"""
import sys

from pymongo import MongoClient

from config import settings

apply_changes = "--apply" in sys.argv

client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
db = client["invoice_db"]

metrics_rows = db["duplicate_metrics"].count_documents({})
flagged_invoices = db["invoices"].count_documents({"duplicate_hits": {"$exists": True}})

total_blocked = 0
for row in db["duplicate_metrics"].find({}, {"total_blocked": 1}):
    total_blocked += row.get("total_blocked", 0)

print(f"duplicate_metrics rows          : {metrics_rows}  (showing as {total_blocked} on the dashboard)")
print(f"invoices carrying duplicate_hits: {flagged_invoices}")

if not apply_changes:
    print("\nDry run. Nothing changed. Re-run with --apply to clear these.")
    sys.exit(0)

deleted = db["duplicate_metrics"].delete_many({}).deleted_count
cleared = db["invoices"].update_many(
    {"duplicate_hits": {"$exists": True}},
    {"$unset": {"duplicate_hits": "", "duplicate_log": "", "last_duplicate_at": ""}},
).modified_count

print(f"\nRemoved {deleted} metric rows and cleared counters on {cleared} invoices.")
print("Duplicates Blocked now reads 0.")
