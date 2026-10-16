"""
Mark invoices that Finance pushed to Zoho by hand as accepted, without this
service pushing anything.

Garima posts some bills in Zoho directly. Those invoices stay "pending" in the
portal, so the dashboard understates what has been dealt with and somebody
could accept one again - creating a second bill in Zoho for an invoice that is
already there.

This closes the gap from our side only. It writes to MongoDB and NEVER calls
zoho_client, so running it cannot create, change or touch anything in Zoho.

Each invoice gets:
    status           -> accepted
    accepted_at      -> now
    pushed_manually  -> true          so these stay distinguishable
    accepted_via     -> "manual"
    zoho_bill_id     -> if given in the file
and one row in zoho_push_metrics tagged source: "manual", so the Pushed figure
matches what is actually in Zoho rather than only what this service sent.

The input is a CSV with a header. vendor_name is required because an invoice
number identifies nothing on its own - CGST Rule 46(b) makes it unique only per
supplier per financial year, so two vendors can both have invoice 237.

    invoice_number,vendor_name,zoho_bill_id
    14-2026-27,MEETING MINDS INFOSYSTEMS,
    237,Cladus,

    python mark_pushed_manually.py pushed.csv
    python mark_pushed_manually.py pushed.csv --apply
"""
import argparse
import asyncio
import csv
import re
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from verification_router import _norm_invoice_number, _norm_vendor_name

NOT_DELETED = {"deleted_at": {"$exists": False}}
ALREADY_SYNCED = ("accepted", "paid")


# Finance writes these by hand, so the column headings vary. Match on what the
# heading means rather than on an exact string, and print what was chosen so a
# wrong guess is visible before anything is written.
#
# Headings are compared with punctuation and spaces removed, so "Inv. No.",
# "Invoice No", "invoice_number" and "INVOICE NUMBER" are all the same thing.
# Most specific hint first: the first one that matches wins.
HEADER_HINTS = {
    "invoice_number": ("invoicenumber", "invoiceno", "invno", "invoiceid",
                       "billnumber", "billno", "invoice", "inv"),
    "vendor_name": ("vendorname", "suppliername", "partyname", "vendor",
                    "supplier", "party"),
    "zoho_bill_id": ("zohobillid", "zohoid", "billid", "zoho"),
}


def _clean_header(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _pick_columns(headers):
    chosen = {}
    cleaned = [(i, _clean_header(h)) for i, h in enumerate(headers)]
    for field, hints in HEADER_HINTS.items():
        for hint in hints:                      # most specific hint first
            for index, header in cleaned:
                if header == hint and index not in chosen.values():
                    chosen[field] = index
                    break
            if field in chosen:
                break
        if field in chosen:
            continue
        for hint in hints:
            for index, header in cleaned:
                if hint in header and index not in chosen.values():
                    chosen[field] = index
                    break
            if field in chosen:
                break
    return chosen


def _rows_from_table(table):
    """table: list of row tuples, the first being the header."""
    header_index = next(
        (i for i, row in enumerate(table)
         if sum(1 for c in row if c is not None and str(c).strip()) >= 2), None)
    if header_index is None:
        raise SystemExit("!! Could not find a header row in that file.")

    headers = table[header_index]
    chosen = _pick_columns(headers)

    print("Columns read from the file:")
    for field in ("invoice_number", "vendor_name", "zoho_bill_id"):
        if field in chosen:
            print(f"   {field:16} <- {str(headers[chosen[field]]).strip()!r}")
        else:
            print(f"   {field:16} <- (not found)")
    print()

    missing = {"invoice_number", "vendor_name"} - set(chosen)
    if missing:
        raise SystemExit(
            f"!! Could not find a column for: {', '.join(sorted(missing))}\n"
            f"   Headings in the file: "
            f"{', '.join(str(h).strip() for h in headers if h is not None and str(h).strip())}\n"
            f"   Rename the column, or add one called 'invoice_number' / 'vendor_name'."
        )

    out = []
    for row in table[header_index + 1:]:
        record = {}
        for field, index in chosen.items():
            value = row[index] if index < len(row) else None
            # Invoice numbers are often numeric-looking; keep them as text and
            # drop the .0 Excel adds when it stores them as floats.
            if isinstance(value, float) and value.is_integer():
                value = str(int(value))
            record[field] = "" if value is None else str(value).strip()
        if record.get("invoice_number") or record.get("vendor_name"):
            out.append(record)
    return out


def read_rows(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.worksheets[0]
        if len(wb.worksheets) > 1:
            print(f"Reading the first sheet, {ws.title!r}, "
                  f"of {len(wb.worksheets)}: {', '.join(s.title for s in wb.worksheets)}\n")
        return _rows_from_table(list(ws.iter_rows(values_only=True)))

    with open(path, newline="", encoding="utf-8-sig") as handle:
        return _rows_from_table([tuple(r) for r in csv.reader(handle)])


async def _record_push(db, doc, now, zoho_bill_id=None, extra=None):
    """Mark one invoice accepted and count it as pushed. Never calls Zoho."""
    update = {
        "status": "accepted",
        "accepted_at": now,
        "updated_at": now,
        "pushed_manually": True,
        "accepted_via": "manual",
    }
    if zoho_bill_id:
        update["zoho_bill_id"] = zoho_bill_id
    update.update(extra or {})

    await db["invoices"].update_one({"_id": doc["_id"]}, {"$set": update})

    data = doc.get("invoice_data") or {}
    await db["zoho_push_metrics"].insert_one({
        "metrics_type": "zoho_push_metrics",
        "total_pushed": 1,
        "vendor_name": doc.get("vendor_name") or data.get("vendor_name"),
        # Tagged, so "what did the automation push" and "what is in Zoho" can
        # still be told apart later.
        "source": "manual",
        "invoice_id": doc["_id"],
        # The invoice's own date, not today's. Every other figure on the
        # dashboard is keyed to when the invoice arrived, so stamping these
        # with the date of the backfill puts a September invoice into
        # October's Pushed and leaves the month's numbers uncomparable.
        "created_at": doc.get("created_at") or now,
    })


async def mark_by_id(db, invoices, args):
    """
    One invoice, by id.

    For records the file cannot match - most often because extraction never
    read an invoice number off the page, so there is nothing to match on.
    --invoice-number fills that in at the same time, which matters beyond
    tidiness: without it the duplicate fingerprint cannot be built, and the
    same invoice arriving again would not be recognised.
    """
    doc = await invoices.find_one({"_id": args.id})
    if not doc:
        print(f"!! No invoice with id {args.id!r}.")
        return
    if doc.get("deleted_at"):
        print(f"!! That invoice is deleted. Restore it first if this is right.")
        return

    data = doc.get("invoice_data") or {}
    current_number = doc.get("invoice_number") or data.get("invoice_number")

    print(f"VENDOR  : {doc.get('vendor_name') or data.get('vendor_name') or '?'}")
    print(f"AMOUNT  : {doc.get('total_amount') or data.get('total_amount') or '?'}")
    print(f"DATE    : {doc.get('invoice_date') or data.get('invoice_date') or '?'}")
    print(f"NUMBER  : {current_number or '(none extracted)'}")
    print(f"STATUS  : {doc.get('status')}")

    if doc.get("status") in ALREADY_SYNCED:
        print("\nAlready accepted or paid. Nothing to do.")
        return

    extra = {}
    if args.invoice_number:
        extra["invoice_number"] = args.invoice_number
        if isinstance(data, dict) and data:
            extra["invoice_data.invoice_number"] = args.invoice_number
        print(f"\nWill set invoice_number to {args.invoice_number!r}"
              f"{' (replacing ' + repr(current_number) + ')' if current_number else ''}")
    elif not current_number:
        print("\nNote: no invoice number on this record. Without one the duplicate")
        print("      check cannot fingerprint it, so the same invoice arriving")
        print("      again would not be recognised. Pass --invoice-number to fix.")

    print("\nWill mark accepted and count as pushed. Nothing goes to Zoho.")

    if not args.apply:
        print("\nDry run. Nothing written.")
        return

    await _record_push(db, doc, datetime.utcnow(), extra=extra)
    print("\nDone.")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="CSV or XLSX: invoice number, vendor name[, zoho bill id]")
    ap.add_argument("--id", default=None,
                    help="one invoice by its id, for records the file cannot match - "
                         "usually because no invoice number was extracted")
    ap.add_argument("--invoice-number", default=None,
                    help="with --id: also fill in the missing invoice number")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    if not args.file and not args.id:
        print("!! Give a file, or --id for a single invoice.")
        return
    if args.invoice_number and not args.id:
        print("!! --invoice-number only applies with --id.")
        return

    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    db = client["invoice_db"]
    invoices = db["invoices"]

    if args.id:
        await mark_by_id(db, invoices, args)
        return

    rows = read_rows(args.file)
    if not rows:
        print("The file has no rows.")
        return

    print(f"{len(rows)} invoice(s) in the file.\n")

    matched, ambiguous, not_found, already = [], [], [], []

    for row in rows:
        number = row["invoice_number"]
        vendor = row["vendor_name"]
        if not number or not vendor:
            not_found.append((row, "invoice_number and vendor_name are both required"))
            continue

        # Match on the normalised invoice number, then narrow by vendor name.
        # Done in Python rather than a regex query so the same normalisation the
        # service uses applies to both sides - a number written 14-2026-27 in
        # the file and 14/2026/27 on the invoice is the same number.
        wanted_number = _norm_invoice_number(number)
        wanted_vendor = _norm_vendor_name(vendor)

        candidates = await invoices.find({
            **NOT_DELETED,
            "$or": [
                {"invoice_number": {"$exists": True}},
                {"invoice_data.invoice_number": {"$exists": True}},
            ],
        }, {
            "invoice_number": 1, "vendor_name": 1, "status": 1, "total_amount": 1,
            "invoice_data.invoice_number": 1, "invoice_data.vendor_name": 1,
            "invoice_data.total_amount": 1, "zoho_bill_id": 1,
        }).to_list(None)

        hits = []
        for doc in candidates:
            data = doc.get("invoice_data") or {}
            doc_number = doc.get("invoice_number") or data.get("invoice_number")
            doc_vendor = doc.get("vendor_name") or data.get("vendor_name")
            if _norm_invoice_number(doc_number) != wanted_number:
                continue
            if _norm_vendor_name(doc_vendor) != wanted_vendor:
                continue
            hits.append(doc)

        if not hits:
            not_found.append((row, "no invoice matches that number and vendor"))
        elif len(hits) > 1:
            ambiguous.append((row, hits))
        elif hits[0].get("status") in ALREADY_SYNCED:
            already.append((row, hits[0]))
        else:
            matched.append((row, hits[0]))

    def describe(doc):
        data = doc.get("invoice_data") or {}
        amount = doc.get("total_amount") or data.get("total_amount") or "?"
        return (f"{doc.get('vendor_name') or data.get('vendor_name') or '?'}  "
                f"{doc.get('invoice_number') or data.get('invoice_number') or '?'}  "
                f"{amount}  [{doc.get('status')}]")

    if matched:
        print(f"WILL MARK ACCEPTED ({len(matched)}):")
        for row, doc in matched:
            bill = f"  zoho_bill_id={row['zoho_bill_id']}" if row.get("zoho_bill_id") else ""
            print(f"   {describe(doc)}{bill}")
            print(f"      id {doc['_id']}")

    if already:
        print(f"\nALREADY ACCEPTED OR PAID ({len(already)}) - left alone:")
        for row, doc in already:
            print(f"   {describe(doc)}")

    if ambiguous:
        print(f"\nMORE THAN ONE MATCH ({len(ambiguous)}) - skipped, resolve by hand:")
        for row, hits in ambiguous:
            print(f"   {row['invoice_number']} / {row['vendor_name']}")
            for doc in hits:
                print(f"      {doc['_id']}   {describe(doc)}")

    if not_found:
        print(f"\nNOT FOUND ({len(not_found)}):")
        for row, why in not_found:
            print(f"   {row.get('invoice_number')} / {row.get('vendor_name')}   {why}")
        print("\n   Check the vendor spelling against the dashboard. These may be")
        print("   invoices that never reached the portal at all - worth knowing")
        print("   either way, since nothing here would have caught them.")

    print("\n" + "-" * 60)
    print(f"{len(matched)} to mark, {len(already)} already done, "
          f"{len(ambiguous)} ambiguous, {len(not_found)} not found")

    if not args.apply:
        print("\nDry run. Nothing written, and nothing was sent to Zoho.")
        return
    if not matched:
        print("\nNothing to do.")
        return

    now = datetime.utcnow()
    for row, doc in matched:
        await _record_push(db, doc, now, zoho_bill_id=row.get("zoho_bill_id"))

    print(f"\nMarked {len(matched)} invoice(s) accepted and counted them as pushed.")
    print("Nothing was sent to Zoho - these were already there.")
    print("\nFind them again with: { pushed_manually: true }")


if __name__ == "__main__":
    asyncio.run(main())
