"""
Onboard new vendors: master sheet -> Zoho -> Vendor-Ledgers.xlsx -> database.

Run this whenever Finance adds rows to the vendor master. It does in one pass
what previously took three commands:

  1. finds vendors in the sheet that are not yet in the database
  2. looks each one up in Zoho Books for its contact id
     (by GSTIN when there is one, otherwise by name)
  3. works out the ledger from that vendor's PAST BILLS in Zoho - Zoho has no
     "default account" field on a contact, but every bill line it has ever
     booked for them names one, so their history answers it
  4. falls back to ledger_map.csv for vendors with no billing history
  5. writes a row to Vendor-Ledgers.xlsx and a document to invoice_db.vendors,
     carrying bank details, credit days, TDS rate and GSTIN or PAN from the sheet

Nothing is guessed. A vendor missing from Zoho, or with no ledger from either
source, is reported and skipped rather than created half-formed.

    python onboard_vendors.py --sheet "Vendor Master.xlsx"
    python onboard_vendors.py --sheet "Vendor Master.xlsx" --apply
"""
import argparse
import asyncio
import difflib
import os
import re
import shutil
import uuid
from collections import Counter
from datetime import datetime

import openpyxl
from motor.motor_asyncio import AsyncIOMotorClient

from config import settings
from zoho_client import zoho_books_client

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_WORKBOOK = os.path.join(HERE, "Vendor-Ledgers.xlsx")
LEDGER_MAP = os.path.join(HERE, "ledger_map.csv")
MAX_BILLS_PER_VENDOR = 12

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def classify_tax_id(value):
    """The sheet's column holds GSTINs and PANs alike. Tell them apart."""
    if not value:
        return None, None, None
    text = norm(value)
    if GSTIN_PATTERN.match(text):
        return text, text[2:12], None
    if PAN_PATTERN.match(text):
        return None, text, None
    return None, None, f"{value!r} is neither a valid GSTIN (15) nor a PAN (10)"


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


def read_ledger_map():
    """Optional vendor -> ledger overrides, for vendors with no billing history."""
    import csv
    if not os.path.exists(LEDGER_MAP):
        return {}
    mapping = {}
    with open(LEDGER_MAP, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            keys = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            vendor = keys.get("vendor")
            ledger_id = next((keys[k] for k in keys if k.startswith("ledger id")), "")
            if vendor and (keys.get("ledger") or ledger_id):
                mapping[norm(vendor)] = {"name": keys.get("ledger"), "id": ledger_id}
    return mapping


async def fetch_all(fetcher):
    out, page = [], 1
    while True:
        batch = await fetcher(page=page, per_page=200)
        if not batch:
            break
        out.extend(batch)
        page += 1
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--apply", action="store_true", help="write to the workbook and the database")
    args = ap.parse_args()

    book = openpyxl.load_workbook(args.sheet)
    sheet = book[book.sheetnames[0]]
    master = []
    for row in sheet.iter_rows(min_row=2):
        if not row[0].value or is_red(row[0]):
            continue
        master.append(dict(
            name=str(row[0].value).strip(), tds=row[1].value, tax_id=row[5].value,
            bank=row[6].value, ifsc=row[7].value, acct=row[8].value, credit=row[9].value,
        ))

    db = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)["invoice_db"]
    existing = await db["vendors"].find({}, {"vendor_name": 1, "gstin": 1, "pan": 1, "_id": 0}).to_list(None)
    known = {norm(v.get("vendor_name")) for v in existing}
    known |= {norm(v.get("gstin")) for v in existing if v.get("gstin")}
    known |= {norm(v.get("pan")) for v in existing if v.get("pan")}

    new = []
    for m in master:
        gstin, pan, problem = classify_tax_id(m["tax_id"])
        m.update(gstin=gstin, pan=pan, tax_problem=problem)
        if norm(m["name"]) in known or (gstin and norm(gstin) in known) or (pan and norm(pan) in known):
            continue
        new.append(m)

    print(f"vendors in the database : {len(existing)}")
    print(f"new in the sheet        : {len(new)}")
    if not new:
        print("\nNothing to onboard.")
        return

    print("\nReading Zoho...")
    contacts = await fetch_all(zoho_books_client.get_vendors)
    accounts = await fetch_all(zoho_books_client.get_chartofaccounts)
    bills = await fetch_all(zoho_books_client.get_bills)
    print(f"  {len(contacts)} contacts, {len(accounts)} accounts, {len(bills)} bills")

    by_name = {norm(c.get("contact_name")): c for c in contacts if c.get("contact_name")}
    by_gstin = {norm(c.get("gst_no")): c for c in contacts if c.get("gst_no")}
    account_name = {str(a.get("account_id")): a.get("account_name") for a in accounts}
    accounts_by_name = {norm(a.get("account_name")): str(a.get("account_id"))
                        for a in accounts if a.get("account_name")}
    bills_by_vendor = {}
    for b in bills:
        bills_by_vendor.setdefault(str(b.get("vendor_id")), []).append(b)

    ledger_map = read_ledger_map()
    ready, no_contact, no_ledger = [], [], []

    print()
    for m in new:
        contact = (by_gstin.get(norm(m["gstin"])) if m["gstin"] else None) or by_name.get(norm(m["name"]))
        if not contact:
            near = difflib.get_close_matches(norm(m["name"]), list(by_name), n=3, cutoff=0.6)
            m["suggestions"] = [by_name[n].get("contact_name") for n in near]
            no_contact.append(m)
            continue

        m["contact_id"] = str(contact.get("contact_id"))
        if not m["gstin"]:
            m["gstin"], _, _ = classify_tax_id(contact.get("gst_no"))

        # Ledger, from this vendor's own billing history first.
        used = Counter()
        for bill in bills_by_vendor.get(m["contact_id"], [])[:MAX_BILLS_PER_VENDOR]:
            detail = await zoho_books_client.get_bill(bill.get("bill_id"))
            for line in (detail or {}).get("line_items", []):
                if line.get("account_id"):
                    used[str(line["account_id"])] += 1

        if used:
            top_id, hits = used.most_common(1)[0]
            m["ledger_id"] = top_id
            m["ledger_name"] = account_name.get(top_id) or f"(account {top_id})"
            m["ledger_source"] = f"past bills, {hits}/{sum(used.values())} lines"
        else:
            mapped = ledger_map.get(norm(m["name"])) or {}
            ledger_id = mapped.get("id") or accounts_by_name.get(norm(mapped.get("name") or ""))
            if not ledger_id:
                m["why"] = "no bills in Zoho and nothing in ledger_map.csv"
                no_ledger.append(m)
                continue
            m["ledger_id"] = ledger_id
            m["ledger_name"] = mapped.get("name") or account_name.get(ledger_id) or f"(account {ledger_id})"
            m["ledger_source"] = "ledger_map.csv"

        ready.append(m)

    print(f"READY ({len(ready)}):")
    for m in ready:
        tax = m["gstin"] or (f"{m['pan']} (PAN)" if m["pan"] else "-")
        bank = str(m["bank"] or "-")
        print(f"   {m['name'][:34]:36} {tax[:17]:18} {m['ledger_name'][:30]:32} {bank[:18]}")
        print(f"   {'':36} zoho={m['contact_id']}  ledger from {m['ledger_source']}")

    if no_ledger:
        print(f"\nNO LEDGER ({len(no_ledger)}) - in Zoho, but never billed and not in ledger_map.csv:")
        for m in no_ledger:
            print(f"   {m['name'][:40]:42} {m['why']}")
        print("   Add a row to ledger_map.csv with the ledger name or account id.")

    if no_contact:
        print(f"\nNOT IN ZOHO ({len(no_contact)}) - create the contact in Zoho Books first:")
        for m in no_contact:
            hint = f"  closest: {', '.join(m['suggestions'])}" if m.get("suggestions") else ""
            print(f"   {m['name'][:40]:42}{hint}")

    bad_ifsc = [m for m in ready if m["ifsc"] and not IFSC_PATTERN.match(str(m["ifsc"]).strip().upper())]
    if bad_ifsc:
        print(f"\nINVALID IFSC ({len(bad_ifsc)}) - stored without it rather than with a value that fails:")
        for m in bad_ifsc:
            print(f"   {m['name'][:40]:42} {m['ifsc']!r}")

    tax_problems = [m for m in new if m.get("tax_problem")]
    if tax_problems:
        print(f"\nCHECK THE GSTIN/PAN COLUMN ({len(tax_problems)}):")
        for m in tax_problems:
            print(f"   {m['name'][:40]:42} {m['tax_problem']}")

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply.")
        return
    if not ready:
        print("\nNothing ready to onboard.")
        return

    docs, rows = [], []
    for m in ready:
        vendor_id = str(uuid.uuid4())
        doc = {
            "vendor_id": vendor_id,
            "vendor_name": m["name"],
            "ledger_name": m["ledger_name"],
            "ledger_id": m["ledger_id"],
            "zoho_contact_id": m["contact_id"],
            "gstin": m["gstin"],
        }
        if m["pan"]:
            doc["pan"] = m["pan"]
        if m["credit"] is not None:
            doc["credit_days"] = int(m["credit"])
        if m["tds"] is not None:
            doc["tds_rate"] = round(float(m["tds"]), 4)

        ifsc = str(m["ifsc"]).strip().upper().replace(" ", "") if m["ifsc"] else None
        if ifsc and not IFSC_PATTERN.match(ifsc):
            ifsc = None
        bank = {k: v for k, v in {
            "bank": str(m["bank"]).strip() if m["bank"] else None,
            "ifsc": ifsc,
            "account_number": account_number(m["acct"]),
        }.items() if v}
        if bank:
            doc["bank_details"] = bank

        docs.append(doc)
        rows.append([m["name"], m["ledger_name"], vendor_id, m["contact_id"], m["gstin"], m["ledger_id"]])

    await db["vendors"].insert_many(docs)
    print(f"\ninserted {len(docs)} vendor(s) into invoice_db.vendors")

    backup = LEDGER_WORKBOOK.replace(".xlsx", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    shutil.copy2(LEDGER_WORKBOOK, backup)
    wb = openpyxl.load_workbook(LEDGER_WORKBOOK)
    for row in rows:
        wb["Sheet1"].append(row)
    wb.save(LEDGER_WORKBOOK)
    print(f"appended {len(rows)} row(s) to Vendor-Ledgers.xlsx (backed up first)")
    print("\nCommit Vendor-Ledgers.xlsx so the repo matches the database.")


if __name__ == "__main__":
    asyncio.run(main())
