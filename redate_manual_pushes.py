"""
Re-date the manual Zoho push rows to the month their invoice belongs to.

mark_pushed_manually.py originally stamped every row it wrote with the time of
the backfill. That put September invoices into October's Pushed figure, so
Pushed (25) ran ahead of Processed (19) for reasons that had nothing to do with
the invoices themselves.

Every other figure on the dashboard is keyed to when the invoice arrived. This
makes Pushed agree, by rebuilding the manual rows from the invoices they
describe - one row per invoice marked pushed_manually, carrying that invoice's
own created_at, plus the invoice_id that the first version did not record.

Safe to re-run: it removes the manual rows and writes them again from the
invoices, so the count cannot drift. Rows written by the service itself
(source absent) are never touched, and nothing here talks to Zoho.

    python redate_manual_pushes.py
    python redate_manual_pushes.py --apply
"""
import argparse
import asyncio
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    db = client["invoice_db"]
    invoices = db["invoices"]
    pushes = db["zoho_push_metrics"]

    manual_rows = await pushes.count_documents({"source": "manual"})
    automated = await pushes.count_documents({"source": {"$exists": False}})

    docs = await invoices.find(
        {"pushed_manually": True, "deleted_at": {"$exists": False}},
        {"vendor_name": 1, "created_at": 1, "invoice_data.vendor_name": 1},
    ).to_list(None)

    print(f"manual push rows now      : {manual_rows}")
    print(f"invoices marked manual    : {len(docs)}")
    print(f"rows written by the service: {automated}  (untouched)\n")

    if manual_rows != len(docs):
        print("!! Those two numbers differ. Rebuilding will make the manual count")
        print("   match the invoices, which is almost certainly what you want -")
        print("   but check why they drifted before applying.\n")

    by_month = Counter()
    undated = 0
    for doc in docs:
        created = doc.get("created_at")
        if created:
            by_month[f"{created:%Y-%m}"] += 1
        else:
            undated += 1

    print("Pushed will be counted in these months instead of all in the backfill month:")
    for month, count in sorted(by_month.items()):
        print(f"   {month}   {count}")
    if undated:
        print(f"   (no created_at)  {undated}  - these keep today's date, "
              f"there is nothing else to use")

    if not args.apply:
        print("\nDry run. Nothing written.")
        return

    from datetime import datetime
    now = datetime.utcnow()

    removed = await pushes.delete_many({"source": "manual"})
    rows = [{
        "metrics_type": "zoho_push_metrics",
        "total_pushed": 1,
        "vendor_name": d.get("vendor_name") or (d.get("invoice_data") or {}).get("vendor_name"),
        "source": "manual",
        "invoice_id": d["_id"],
        "created_at": d.get("created_at") or now,
    } for d in docs]

    if rows:
        await pushes.insert_many(rows)

    print(f"\nremoved {removed.deleted_count} manual row(s), wrote {len(rows)} back "
          f"dated to their invoice")
    print("Nothing was sent to Zoho.")


if __name__ == "__main__":
    asyncio.run(main())
