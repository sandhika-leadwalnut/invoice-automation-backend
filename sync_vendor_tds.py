"""
Load TDS rates from the vendor master into invoice_db.vendors.

The payment sheet now pays net of TDS, so a missing rate is not a cosmetic
gap - it means the vendor is paid the gross and the tax is never withheld.

A blank TDS cell is read as zero. That is the sheet's convention and it is
unambiguous: no rate means nothing is deducted. An unreadable value is NOT
read as zero - it is reported and skipped, because quietly turning "0.1 " or
"10%" into no deduction at all is the failure this is meant to prevent.

Rates may be written either way round and are stored as decimals:

    0.1  ->  0.1    (10%)
    10   ->  0.1    (10%, written as a percentage)
    2    ->  0.02
    0    ->  0

    python sync_vendor_tds.py "Vendor Master.xlsx"
    python sync_vendor_tds.py "Vendor Master.xlsx" --apply
"""
import argparse
import asyncio
import re

from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from verification_router import _norm_vendor_name

HEADER_HINTS = {
    "vendor_name": ("vendorname", "suppliername", "partyname", "vendor", "supplier", "party"),
    "tds": ("tdsrate", "tdspercent", "tds", "tdsdeduction", "withholding"),
}


def _clean_header(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _pick_columns(headers):
    chosen, cleaned = {}, [(i, _clean_header(h)) for i, h in enumerate(headers)]
    for field, hints in HEADER_HINTS.items():
        for hint in hints:
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


def parse_rate(value):
    """
    (rate, problem). Blank is zero; anything unparseable is a problem, never
    silently zero.
    """
    if value is None or str(value).strip() in ("", "-", "nil", "NIL", "na", "NA"):
        return 0.0, None
    text = str(value).strip().rstrip("%").replace(",", "").strip()
    try:
        number = float(text)
    except ValueError:
        return None, f"{value!r} is not a number"
    if number < 0:
        return None, f"{value!r} is negative"
    # Above 1 can only be a percentage: no TDS rate is 100%.
    rate = number / 100 if number > 1 else number
    if rate > 0.5:
        return None, f"{value!r} would be {rate:.0%}, which is not a TDS rate"
    return round(rate, 4), None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", help="the vendor master (.xlsx or .csv)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.sheet.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(args.sheet, data_only=True)
        table = list(wb.worksheets[0].iter_rows(values_only=True))
    else:
        import csv
        with open(args.sheet, newline="", encoding="utf-8-sig") as handle:
            table = [tuple(r) for r in csv.reader(handle)]

    header_index = next(
        (i for i, row in enumerate(table)
         if sum(1 for c in row if c is not None and str(c).strip()) >= 2), None)
    if header_index is None:
        print("!! Could not find a header row.")
        return

    headers = table[header_index]
    chosen = _pick_columns(headers)

    print("Columns read from the sheet:")
    for field in ("vendor_name", "tds"):
        found = headers[chosen[field]] if field in chosen else None
        print(f"   {field:12} <- {found!r}" if found else f"   {field:12} <- (not found)")
    print()

    if "vendor_name" not in chosen or "tds" not in chosen:
        print("!! Need a vendor name column and a TDS column.")
        print(f"   Headings: {', '.join(str(h).strip() for h in headers if h)}")
        return

    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    vendors = client["invoice_db"]["vendors"]
    docs = await vendors.find({}).to_list(None)

    by_name = {}
    for doc in docs:
        for name in [doc.get("vendor_name")] + list(doc.get("aliases") or []):
            if name:
                by_name.setdefault(_norm_vendor_name(name), doc)

    changes, unchanged, unmatched, problems = [], 0, [], []

    for row in table[header_index + 1:]:
        name = row[chosen["vendor_name"]] if chosen["vendor_name"] < len(row) else None
        if not name or not str(name).strip():
            continue
        raw = row[chosen["tds"]] if chosen["tds"] < len(row) else None

        rate, problem = parse_rate(raw)
        if problem:
            problems.append((str(name).strip(), problem))
            continue

        doc = by_name.get(_norm_vendor_name(name))
        if not doc:
            unmatched.append(str(name).strip())
            continue

        current = doc.get("tds_rate")
        current = float(current) if current is not None else None
        if current == rate:
            unchanged += 1
            continue
        changes.append((doc, current, rate))

    if changes:
        print(f"WILL CHANGE ({len(changes)}):")
        for doc, current, rate in changes:
            was = "(none)" if current is None else f"{current:.2%}"
            print(f"   {doc.get('vendor_name')[:40]:42} {was:>8}  ->  {rate:.2%}")

    if problems:
        print(f"\nCOULD NOT READ THE TDS VALUE ({len(problems)}) - skipped, not set to zero:")
        for name, why in problems:
            print(f"   {name[:40]:42} {why}")

    if unmatched:
        print(f"\nNOT IN THE DATABASE ({len(unmatched)}) - nothing to update:")
        for name in unmatched[:20]:
            print(f"   {name}")
        if len(unmatched) > 20:
            print(f"   ... and {len(unmatched) - 20} more")

    print("\n" + "-" * 60)
    print(f"{len(changes)} to change, {unchanged} already correct, "
          f"{len(unmatched)} not in the database, {len(problems)} unreadable")

    zeroed = [c for c in changes if c[2] == 0]
    if zeroed:
        print(f"\n{len(zeroed)} vendor(s) go to 0% because the cell is blank. They will")
        print("be paid the gross amount. Worth a look if any should be deducting.")

    if not args.apply:
        print("\nDry run. Nothing written.")
        return

    for doc, _, rate in changes:
        await vendors.update_one({"vendor_id": doc["vendor_id"]}, {"$set": {"tds_rate": rate}})

    print(f"\nUpdated {len(changes)} vendor(s).")


if __name__ == "__main__":
    asyncio.run(main())
