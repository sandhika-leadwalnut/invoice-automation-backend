"""
Mark pending invoices paid without pushing them to Zoho.

For a backlog settled outside the system: invoices paid before the portal
existed, or handled by hand, which would otherwise sit in Pending for ever and
make the queue meaningless.

These are NOT counted as pushed. They never reached Zoho, and adding a push row
would say they are in the books when they are not. Each one is stamped
paid_via "outside_system" so they stay distinguishable from invoices the
service actually processed:

    { paid_via: "outside_system" }

Nothing here talks to Zoho.

    python mark_pending_as_paid.py --exclude "SUMO"
    python mark_pending_as_paid.py --exclude "SUMO" --paid-on 2026-11-03 --apply
"""
import argparse
import asyncio
import re
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings

NOT_DELETED = {"deleted_at": {"$exists": False}}


def _key(value):
    """An invoice number comparable across spacing and punctuation."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="pending",
                    help="which status to take, default 'pending'")
    ap.add_argument("--exclude", action="append", default=[],
                    help="vendor name to leave alone; repeatable, matched loosely")
    ap.add_argument("--exclude-number", action="append", default=[],
                    help="an invoice number to leave alone; exact, after ignoring "
                         "case and punctuation, so SGI/2996/26-27 cannot also "
                         "catch SGI/1556/26-27")
    ap.add_argument("--exclude-id", action="append", default=[],
                    help="a specific invoice id to leave alone")
    ap.add_argument("--paid-on", default=None,
                    help="YYYY-MM-DD the money went out; defaults to today")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.paid_on:
        try:
            paid_at = datetime.fromisoformat(args.paid_on)
        except ValueError:
            print(f"!! {args.paid_on!r} is not a date. Use YYYY-MM-DD.")
            return
    else:
        paid_at = datetime.utcnow()

    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    invoices = client["invoice_db"]["invoices"]

    docs = await invoices.find(
        {"status": args.status, **NOT_DELETED},
        {"vendor_name": 1, "invoice_number": 1, "total_amount": 1, "status": 1,
         "invoice_data.vendor_name": 1, "invoice_data.invoice_number": 1,
         "invoice_data.total_amount": 1},
    ).to_list(None)

    if not docs:
        print(f"No invoices with status {args.status!r}.")
        return

    patterns = [re.compile(re.escape(e), re.IGNORECASE) for e in args.exclude]
    excluded_ids = set(args.exclude_id)
    # Exact match on the number, not a substring: "SGI/2996/26-27" must not
    # also exclude "SGI/1556/26-27", and one invoice left out by accident is
    # one that stays in Pending without anyone noticing.
    excluded_numbers = {_key(n) for n in args.exclude_number}

    taking, leaving = [], []
    for doc in docs:
        data = doc.get("invoice_data") or {}
        name = doc.get("vendor_name") or data.get("vendor_name") or ""
        number = doc.get("invoice_number") or data.get("invoice_number") or ""

        if (doc["_id"] in excluded_ids
                or _key(number) in excluded_numbers
                or any(p.search(name) for p in patterns)):
            leaving.append((doc, name))
        else:
            taking.append((doc, name))

    def describe(doc, name):
        data = doc.get("invoice_data") or {}
        number = doc.get("invoice_number") or data.get("invoice_number") or "?"
        amount = doc.get("total_amount") or data.get("total_amount") or "?"
        return f"{name[:30]:32} {str(number)[:20]:22} {amount}"

    if leaving:
        print(f"LEAVING ALONE ({len(leaving)}):")
        for doc, name in leaving:
            print(f"   {describe(doc, name)}")
        print()

    print(f"WILL MARK PAID ({len(taking)})  paid_at = {paid_at:%Y-%m-%d %H:%M}:")
    for doc, name in taking:
        print(f"   {describe(doc, name)}")
        print(f"      id {doc['_id']}")

    print("\n" + "-" * 66)
    print(f"{len(taking)} to mark paid, {len(leaving)} left alone")
    print("\nThese are NOT counted as pushed to Zoho, because they were not.")
    print("If they have not been booked in Zoho by hand either, the cost is")
    print("recorded nowhere - worth confirming with Finance before applying.")

    if not args.apply:
        print("\nDry run. Nothing written, and nothing was sent to Zoho.")
        return
    if not taking:
        print("\nNothing to do.")
        return

    now = datetime.utcnow()
    result = await invoices.update_many(
        {"_id": {"$in": [doc["_id"] for doc, _ in taking]}, "status": args.status, **NOT_DELETED},
        {"$set": {
            "status": "paid",
            "paid_at": paid_at,
            "paid_recorded_at": now,
            # So these can always be told apart from invoices the service
            # actually processed and pushed.
            "paid_via": "outside_system",
            "updated_at": now,
        }},
    )

    print(f"\nMarked {result.modified_count} invoice(s) paid.")
    print("Nothing was sent to Zoho.")
    print("\nFind them again with: { paid_via: \"outside_system\" }")


if __name__ == "__main__":
    asyncio.run(main())
