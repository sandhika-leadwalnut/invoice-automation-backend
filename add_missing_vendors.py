"""
Bring vendors that are in the master sheet but not yet in the system into both
places, in two deliberate steps.

    STAGE   resolve each vendor against Zoho Books and append a row to
            Vendor-Ledgers.xlsx. Nothing touches the database yet, so the
            workbook is the thing you review.

    PUSH    read Vendor-Ledgers.xlsx and insert into invoice_db.vendors anything
            that isn't there. The workbook is the source of truth; the database
            follows it.

Two IDs cannot be invented and both come from Zoho:

    zoho_contact_id   found by GSTIN first (a server-side lookup), falling back
                      to contact name. A vendor that isn't in Zoho cannot be
                      added here - create the contact in Zoho first.
    ledger_id         resolved from a ledger NAME against the chart of accounts.
                      Which expense ledger a vendor belongs to is an accounting
                      decision, so it is never guessed: give a "Ledger" column
                      in the master sheet, or --ledger for the whole run.

    python add_missing_vendors.py --sheet "Vendor Master.xlsx"
    python add_missing_vendors.py --sheet "Vendor Master.xlsx" --ledger "..." --stage
    python add_missing_vendors.py --push
    python add_missing_vendors.py --push --sheet "Vendor Master.xlsx"
"""
import argparse
import asyncio
import difflib
import os
import re
import shutil
import uuid
from datetime import datetime

import openpyxl
from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from zoho_client import zoho_books_client

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_WORKBOOK = os.path.join(HERE, "Vendor-Ledgers.xlsx")

# Column order in Vendor-Ledgers.xlsx.
COL_VENDOR, COL_LEDGER, COL_VENDOR_ID, COL_ZOHO, COL_GSTIN, COL_LEDGER_ID = range(6)


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


# The master sheet's column is "GSTIN/PAN" and genuinely holds both. A GSTIN is
# 15 characters - two state digits, a ten-character PAN, an entity code, a
# filler and a checksum. A PAN on its own is ten. Zoho rejects anything that is
# not a real GSTIN outright ("Invalid value passed for gst_no"), so the two have
# to be told apart before a lookup is attempted.
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def classify_tax_id(value):
    """Return (gstin, pan, problem) for a value from the GSTIN/PAN column."""
    if not value:
        return None, None, None
    text = norm(value)
    if GSTIN_PATTERN.match(text):
        return text, text[2:12], None
    if PAN_PATTERN.match(text):
        # Unregistered vendor: they have a PAN but no GST registration.
        return None, text, None
    return None, None, f"{value!r} is neither a valid GSTIN (15 chars) nor a PAN (10)"


def account_number(value):
    if value is None:
        return None
    if isinstance(value, float):
        return str(int(value))
    return re.sub(r"\s+", "", str(value).strip())


def is_red(cell):
    try:
        rgb = cell.fill.fgColor.rgb
        return isinstance(rgb, str) and rgb.upper().endswith("CC0000")
    except Exception:
        return False


def read_ledger_workbook():
    sheet = openpyxl.load_workbook(LEDGER_WORKBOOK)["Sheet1"]
    rows = []
    for r in sheet.iter_rows(min_row=2, values_only=True):
        if r and r[COL_VENDOR]:
            rows.append({
                "vendor_name": str(r[COL_VENDOR]).strip(),
                "ledger_name": r[COL_LEDGER],
                "vendor_id": r[COL_VENDOR_ID],
                "zoho_contact_id": r[COL_ZOHO],
                "gstin": r[COL_GSTIN],
                "ledger_id": r[COL_LEDGER_ID],
            })
    return rows


def read_ledger_map(path):
    """
    vendor -> ledger name, from a two-column CSV.

    Lets the ledger decision live in its own reviewable file instead of forcing
    a new column into the master sheet, which Finance also edits.
    """
    import csv
    mapping = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            keys = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            vendor = keys.get("vendor") or keys.get("vendor name") or keys.get("party name")
            ledger = keys.get("ledger") or keys.get("suggested ledger") or keys.get("ledger name")
            # A ledger id given here wins over resolving the name. Zoho's
            # chart-of-accounts listing does not return every account - "Staff
            # Welfare" is active and usable but absent from it - so a name
            # lookup alone cannot reach them.
            ledger_id = next((keys[k] for k in keys if k.startswith("ledger id")), "")
            if vendor and (ledger or ledger_id):
                mapping[norm(vendor)] = {"name": ledger, "id": ledger_id}
    return mapping


def read_master_sheet(path):
    book = openpyxl.load_workbook(path)
    sheet = book[book.sheetnames[0]]
    headers = [str(c.value).strip().lower() if c.value else "" for c in next(sheet.iter_rows(max_row=1))]
    ledger_col = headers.index("ledger") if "ledger" in headers else None

    out = []
    for row in sheet.iter_rows(min_row=2):
        if not row[0].value or is_red(row[0]):
            continue
        out.append({
            "name": str(row[0].value).strip(),
            "gstin": str(row[5].value).strip() if row[5].value else None,
            "tds": row[1].value,
            "bank": row[6].value,
            "ifsc": row[7].value,
            "account": row[8].value,
            "credit": row[9].value,
            "ledger": row[ledger_col].value if ledger_col is not None and ledger_col < len(row) else None,
        })
    return out


async def fetch_all(fetcher):
    out, page = [], 1
    while True:
        batch = await fetcher(page=page, per_page=200)
        if not batch:
            break
        out.extend(batch)
        page += 1
    return out


async def stage(args):
    """Resolve against Zoho and append rows to the workbook."""
    master = read_master_sheet(args.sheet)
    ledger_map = read_ledger_map(args.ledger_map) if args.ledger_map else {}
    if ledger_map:
        print(f"ledger map loaded           : {len(ledger_map)} vendors")
    ledger_rows = read_ledger_workbook()
    known_names = {norm(r["vendor_name"]) for r in ledger_rows}
    known_gstins = {norm(r["gstin"]) for r in ledger_rows if r["gstin"]}

    missing = [
        m for m in master
        if norm(m["name"]) not in known_names
        and not (m["gstin"] and norm(m["gstin"]) in known_gstins)
    ]

    print(f"rows in Vendor-Ledgers.xlsx : {len(ledger_rows)}")
    print(f"in the master sheet only    : {len(missing)}")
    if not missing:
        return

    print("\nResolving against Zoho Books...")
    contacts = await fetch_all(zoho_books_client.get_vendors)
    accounts = await fetch_all(zoho_books_client.get_chartofaccounts)
    print(f"  {len(contacts)} vendor contacts, {len(accounts)} accounts")

    by_name = {norm(c.get("contact_name")): c for c in contacts if c.get("contact_name")}
    by_gstin = {norm(c.get("gst_no")): c for c in contacts if c.get("gst_no")}
    accounts_by_name = {norm(a.get("account_name")): a.get("account_id")
                        for a in accounts if a.get("account_name")}

    ready, no_contact, no_ledger, bad_tax_id = [], [], [], []
    for m in missing:
        gstin, pan, problem = classify_tax_id(m["gstin"])
        m["gstin"], m["pan"] = gstin, pan
        if problem:
            m["tax_id_problem"] = problem
            bad_tax_id.append(m)

        # A real GSTIN is the reliable key - names differ between the sheet and
        # Zoho constantly. A PAN cannot be looked up, so those fall to the name.
        contact = by_gstin.get(gstin) if gstin else None
        if not contact and gstin:
            try:
                contact = await zoho_books_client.get_vendor_by_gstin(gstin)
            except Exception as e:
                # One rejected value must not abandon the other 27.
                print(f"   (Zoho lookup failed for {m['name']}: {e})")
        if not contact:
            contact = by_name.get(norm(m["name"]))
        if not contact:
            near = difflib.get_close_matches(norm(m["name"]), list(by_name), n=3, cutoff=0.6)
            m["suggestions"] = [by_name[n].get("contact_name") for n in near]
            no_contact.append(m)
            continue

        m["contact_id"] = contact.get("contact_id")
        m["zoho_name"] = contact.get("contact_name")
        # Zoho's own GSTIN wins when the sheet had a PAN or something malformed.
        zoho_gstin, _, _ = classify_tax_id(contact.get("gst_no"))
        m["gstin"] = m["gstin"] or zoho_gstin

        mapped = ledger_map.get(norm(m["name"])) or {}
        ledger_name = m["ledger"] or mapped.get("name") or args.ledger
        ledger_id = mapped.get("id") or None

        if not (ledger_name or ledger_id):
            m["why"] = "no ledger given"
            no_ledger.append(m)
            continue

        if not ledger_id:
            ledger_id = accounts_by_name.get(norm(ledger_name))
        if not ledger_id:
            m["why"] = (f"ledger {ledger_name!r} is not in the chart of accounts - "
                        f"put its account id in the Ledger ID column instead")
            no_ledger.append(m)
            continue
        ledger_name = ledger_name or f"(account {ledger_id})"

        m["ledger_name"] = ledger_name
        m["ledger_id"] = str(ledger_id)
        ready.append(m)

    print(f"\nREADY ({len(ready)}):")
    for m in ready:
        print(f"   {m['name'][:40]:42} zoho={m['contact_id']}  {m['ledger_name'][:32]}")

    if no_ledger:
        print(f"\nNEEDS A LEDGER ({len(no_ledger)}) - in Zoho, but no expense ledger:")
        for m in no_ledger:
            print(f"   {m['name'][:40]:42} {m['why']}")

    if no_contact:
        print(f"\nNOT IN ZOHO ({len(no_contact)}) - create the contact in Zoho Books first:")
        for m in no_contact:
            hint = f"  closest: {', '.join(m['suggestions'])}" if m.get("suggestions") else ""
            print(f"   {m['name'][:40]:42}{hint}")

    if bad_tax_id:
        print(f"\nCHECK THE GSTIN/PAN COLUMN ({len(bad_tax_id)}) - these are neither:")
        for m in bad_tax_id:
            print(f"   {m['name'][:40]:42} {m['tax_id_problem']}")
        print("   Staged without a GSTIN. Fix the sheet and re-run to fill it in.")

    if not args.stage:
        print("\nReport only. Re-run with --stage to append the READY rows to the workbook.")
        return
    if not ready:
        print("\nNothing ready to stage.")
        return

    backup = LEDGER_WORKBOOK.replace(".xlsx", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    shutil.copy2(LEDGER_WORKBOOK, backup)
    print(f"\nbacked up the workbook to {os.path.basename(backup)}")

    book = openpyxl.load_workbook(LEDGER_WORKBOOK)
    sheet = book["Sheet1"]
    for m in ready:
        sheet.append([m["name"], m["ledger_name"], str(uuid.uuid4()),
                      str(m["contact_id"]), m["gstin"], m["ledger_id"]])
    book.save(LEDGER_WORKBOOK)
    print(f"appended {len(ready)} row(s) to Vendor-Ledgers.xlsx")
    print("Review the workbook, then run:  python add_missing_vendors.py --push")


async def partial(args):
    """
    Insert sheet-only vendors with just what the master sheet holds.

    No Zoho lookup, no ledger. The record carries vendor_name, GSTIN or PAN,
    bank details, credit days and TDS - enough for a payment date and a payment
    sheet row, which is what Finance is blocked on.

    What it does NOT give you: zoho_contact_id and ledger_id. Without those,
    resolve_vendor_zoho_contact still falls through to the Zoho API lookup and
    the bill's line items get DEFAULT_ITEM_ID instead of the right expense
    account - so these invoices reach Zoho miscoded until the mapping is filled
    in. Each record is flagged needs_zoho_mapping so they are easy to find.
    """
    master = read_master_sheet(args.sheet)
    db = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)["invoice_db"]
    existing = await db["vendors"].find({}, {"vendor_name": 1, "gstin": 1, "_id": 0}).to_list(None)
    known_names = {norm(v.get("vendor_name")) for v in existing}
    known_gstins = {norm(v.get("gstin")) for v in existing if v.get("gstin")}

    docs, rows, skipped = [], [], []
    for m in master:
        if norm(m["name"]) in known_names:
            continue
        gstin, pan, problem = classify_tax_id(m["gstin"])
        if gstin and norm(gstin) in known_gstins:
            continue

        doc = {
            "vendor_id": str(uuid.uuid4()),
            "vendor_name": m["name"],
            "gstin": gstin,
            "zoho_contact_id": None,
            "ledger_id": None,
            "ledger_name": None,
            "needs_zoho_mapping": True,
        }
        if pan:
            doc["pan"] = pan
        if m["credit"] is not None:
            doc["credit_days"] = int(m["credit"])
        if m["tds"] is not None:
            doc["tds_rate"] = round(float(m["tds"]), 4)
        bank = {k: v for k, v in {
            "bank": str(m["bank"]).strip() if m["bank"] else None,
            "ifsc": str(m["ifsc"]).strip().upper() if m["ifsc"] else None,
            "account_number": account_number(m["account"]),
        }.items() if v}
        if bank:
            doc["bank_details"] = bank
        if problem:
            skipped.append((m["name"], problem))

        docs.append(doc)
        rows.append([m["name"], None, doc["vendor_id"], None, gstin, None])

    print(f"vendors already in the database : {len(existing)}")
    print(f"to insert from the master sheet : {len(docs)}")
    print()
    print(f"{'VENDOR':44} {'GSTIN / PAN':18} {'CREDIT':7} BANK")
    for d in docs:
        tax = d.get("gstin") or (d.get("pan", "") + " (PAN)") or "-"
        bank = d.get("bank_details", {}).get("bank", "-")
        print(f"{d['vendor_name'][:43]:44} {tax[:17]:18} {str(d.get('credit_days', '-')):7} {bank}")

    if skipped:
        print(f"\nGSTIN/PAN column looks wrong for {len(skipped)} - stored without either:")
        for name, why in skipped:
            print(f"   {name[:40]:42} {why}")

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply to insert.")
        return
    if not docs:
        print("\nNothing to insert.")
        return

    await db["vendors"].insert_many(docs)
    print(f"\ninserted {len(docs)} vendor(s) into invoice_db.vendors")

    backup = LEDGER_WORKBOOK.replace(".xlsx", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    shutil.copy2(LEDGER_WORKBOOK, backup)
    book = openpyxl.load_workbook(LEDGER_WORKBOOK)
    for row in rows:
        book["Sheet1"].append(row)
    book.save(LEDGER_WORKBOOK)
    print(f"appended {len(rows)} row(s) to Vendor-Ledgers.xlsx (ledger and Zoho columns blank)")
    print()
    print("These vendors can now be paid, but their bills will reach Zoho against")
    print("the default item rather than the right ledger. Fill in the mapping with:")
    print("   python add_missing_vendors.py --sheet \"...\" --stage")


async def push(args):
    """Insert workbook rows that aren't in the database yet."""
    ledger_rows = read_ledger_workbook()
    db = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)["invoice_db"]
    existing = await db["vendors"].find({}, {"vendor_id": 1, "vendor_name": 1, "_id": 0}).to_list(None)
    known_ids = {v.get("vendor_id") for v in existing}
    known_names = {norm(v.get("vendor_name")) for v in existing}

    # Terms live in the master sheet, not the workbook, so attach them if given.
    terms_by_key = {}
    if args.sheet:
        for m in read_master_sheet(args.sheet):
            entry = {}
            if m["credit"] is not None:
                entry["credit_days"] = int(m["credit"])
            if m["tds"] is not None:
                entry["tds_rate"] = round(float(m["tds"]), 4)
            bank = {k: v for k, v in {
                "bank": str(m["bank"]).strip() if m["bank"] else None,
                "ifsc": str(m["ifsc"]).strip().upper() if m["ifsc"] else None,
                "account_number": account_number(m["account"]),
            }.items() if v}
            if bank:
                entry["bank_details"] = bank
            if entry:
                terms_by_key[norm(m["name"])] = entry
                if m["gstin"]:
                    terms_by_key[norm(m["gstin"])] = entry

    to_insert, incomplete = [], []
    for row in ledger_rows:
        if row["vendor_id"] in known_ids or norm(row["vendor_name"]) in known_names:
            continue
        if not (row["vendor_id"] and row["zoho_contact_id"] and row["ledger_id"]):
            incomplete.append(row)
            continue

        gstin, pan, _ = classify_tax_id(row["gstin"])
        doc = {
            "vendor_id": str(row["vendor_id"]),
            "vendor_name": row["vendor_name"],
            "ledger_name": row["ledger_name"],
            "ledger_id": str(row["ledger_id"]),
            "zoho_contact_id": str(row["zoho_contact_id"]),
            # Only a real GSTIN goes in this field - resolve_vendor_zoho_contact
            # matches invoices on it, and a PAN sitting there would never match.
            "gstin": gstin,
        }
        if pan:
            doc["pan"] = pan
        doc.update(terms_by_key.get(norm(row["vendor_name"]))
                   or terms_by_key.get(norm(row["gstin"])) or {})
        to_insert.append(doc)

    print(f"vendors in the database   : {len(existing)}")
    print(f"rows in the workbook      : {len(ledger_rows)}")
    print(f"new, ready to insert      : {len(to_insert)}")
    if incomplete:
        print(f"incomplete workbook rows  : {len(incomplete)}  (missing vendor_id, zoho id or ledger_id)")
        for row in incomplete[:10]:
            print(f"   {row['vendor_name']}")

    for doc in to_insert:
        terms = "credit+bank" if "bank_details" in doc else ("credit" if "credit_days" in doc else "-")
        print(f"   {doc['vendor_name'][:40]:42} zoho={doc['zoho_contact_id']}  terms={terms}")

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply to insert.")
        return
    if not to_insert:
        print("\nNothing to insert.")
        return

    await db["vendors"].insert_many(to_insert)
    print(f"\ninserted {len(to_insert)} vendor(s) into invoice_db.vendors")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=None, help="path to the vendor master xlsx")
    ap.add_argument("--ledger", default=None, help="ledger name to use for every new vendor")
    ap.add_argument("--ledger-map", default=None,
                    help="CSV of vendor,ledger - one ledger name per vendor")
    ap.add_argument("--stage", action="store_true", help="append resolved rows to Vendor-Ledgers.xlsx")
    ap.add_argument("--push", action="store_true", help="insert workbook rows into the database")
    ap.add_argument("--partial", action="store_true",
                    help="insert straight from the master sheet with bank details and "
                         "GSTIN/PAN only, leaving the Zoho contact and ledger blank")
    ap.add_argument("--apply", action="store_true", help="with --push or --partial, actually insert")
    args = ap.parse_args()

    if args.partial:
        if not args.sheet:
            ap.error("--partial needs --sheet")
        await partial(args)
    elif args.push:
        await push(args)
    elif args.sheet:
        await stage(args)
    else:
        ap.error("give --sheet to stage from the master sheet, or --push to load the workbook")


if __name__ == "__main__":
    asyncio.run(main())
