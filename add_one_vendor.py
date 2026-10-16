"""
Add a single vendor to invoice_db.vendors and Vendor-Ledgers.xlsx.

onboard_vendors.py reads the whole master sheet, which is right when Finance has
added a batch of rows. This is for the other case: one vendor, details in hand,
nothing else in the sheet ready to go.

Both IDs still come from Zoho and neither is invented:

    zoho_contact_id   looked up by contact name. A vendor not in Zoho cannot be
                      added - create the contact there first.
    ledger_id         resolved from a ledger NAME against the chart of accounts.
                      Zoho has no default ledger on a contact, so for a vendor
                      with no billing history there is nothing to infer it from.
                      Which ledger a vendor belongs to is an accounting decision.

    python add_one_vendor.py "Rabbani Kaja Mohideen" \
        --ledger "Software Development & Technical Manpower Charges" \
        --pan AMRPK7254P --bank "ICICI Bank" --ifsc ICIC0000297 \
        --account 29701002527 --tds 0.1

    ... then the same line with --apply

Note: anything added here is invisible to the vendor master sheet. Add the row
there too, or the next rebuild from the sheet will not have this vendor.
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

IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


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
    ap.add_argument("name", help="vendor name, as it is spelled in Zoho Books")
    ap.add_argument("--ledger", default=None, help="exact ledger name from the Zoho chart of accounts")
    ap.add_argument("--no-ledger", action="store_true",
                    help="add the vendor before the ledger has been decided. Everything "
                         "except the Zoho push works; see the warning it prints.")
    ap.add_argument("--gstin", default=None)
    ap.add_argument("--pan", default=None)
    ap.add_argument("--bank", default=None)
    ap.add_argument("--ifsc", default=None)
    ap.add_argument("--account", default=None, help="quote it, to keep leading zeros")
    ap.add_argument("--credit-days", type=int, default=None)
    ap.add_argument("--tds", type=float, default=None, help="decimal, e.g. 0.1 for 10%%")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not args.ledger and not args.no_ledger:
        print("!! Give --ledger, or --no-ledger to add this vendor before the")
        print("   ledger has been decided.")
        return
    if args.ledger and args.no_ledger:
        print("!! Give one of --ledger or --no-ledger, not both.")
        return

    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
    vendors = client["invoice_db"]["vendors"]

    existing = await vendors.find_one({"vendor_name": re.compile(f"^{re.escape(args.name)}$", re.IGNORECASE)})
    if existing:
        print(f"!! {args.name!r} is already in the database as {existing.get('vendor_id')}.")
        print("   Use set_vendor_terms.py to change its terms instead.")
        return

    # --- identity -----------------------------------------------------------
    gstin = (args.gstin or "").strip().upper() or None
    pan = (args.pan or "").strip().upper() or None
    if gstin and not GSTIN_PATTERN.match(gstin):
        print(f"!! {gstin!r} is not a valid GSTIN (15 characters).")
        return
    if pan and not PAN_PATTERN.match(pan):
        print(f"!! {pan!r} is not a valid PAN (10 characters).")
        return
    if not gstin and not pan:
        print("!! Give --gstin or --pan. Without one, invoices cannot be matched to this vendor.")
        return

    ifsc = None
    if args.ifsc:
        ifsc = args.ifsc.strip().upper().replace(" ", "")
        if not IFSC_PATTERN.match(ifsc):
            print(f"!! {ifsc!r} is not a valid IFSC (4 letters, a zero, then 6 characters).")
            return

    account = None
    if args.account:
        # Text, never a number: a mangled account number is a failed payment,
        # and leading zeros are real.
        account = re.sub(r"\s+", "", str(args.account).strip())
        if not account.isalnum():
            print(f"!! {account!r} does not look like an account number.")
            return

    bank = {k: v for k, v in {
        "bank": args.bank.strip() if args.bank else None,
        "ifsc": ifsc,
        "account_number": account,
    }.items() if v}
    if bank and len(bank) < 3:
        missing = [f for f in ("bank", "ifsc", "account_number") if f not in bank]
        print(f"!! Bank details are incomplete - missing {', '.join(missing)}.")
        print("   A payment sheet row needs all three. Give them together or none.")
        return

    # --- Zoho ---------------------------------------------------------------
    print("Reading Zoho...")
    contacts = await fetch_all(zoho_books_client.get_vendors)
    accounts = await fetch_all(zoho_books_client.get_chartofaccounts)
    print(f"  {len(contacts)} contacts, {len(accounts)} accounts\n")

    target = norm(args.name)
    contact = next((c for c in contacts if norm(c.get("contact_name")) == target), None)
    if not contact:
        close = difflib.get_close_matches(
            args.name, [c.get("contact_name", "") for c in contacts], n=5, cutoff=0.6)
        print(f"!! {args.name!r} is not a contact in Zoho Books. Create it there first.")
        if close:
            print(f"   Closest names in Zoho: {', '.join(close)}")
        return

    account_doc = None
    if args.ledger:
        wanted = norm(args.ledger)
        account_doc = next((a for a in accounts if norm(a.get("account_name")) == wanted), None)
        if not account_doc:
            close = difflib.get_close_matches(
                args.ledger, [a.get("account_name", "") for a in accounts], n=5, cutoff=0.5)
            print(f"!! {args.ledger!r} is not a ledger in the Zoho chart of accounts.")
            if close:
                print(f"   Did you mean: {', '.join(close)}")
            print("   Run: python list_zoho.py accounts --search <word>")
            return

    # --- the document -------------------------------------------------------
    vendor_id = str(uuid.uuid4())
    doc = {
        "vendor_id": vendor_id,
        "vendor_name": args.name,
        "zoho_contact_id": str(contact["contact_id"]),
        "gstin": gstin,
    }
    if account_doc:
        doc["ledger_name"] = account_doc["account_name"]
        doc["ledger_id"] = str(account_doc["account_id"])
    else:
        # Recorded, so the gap is queryable rather than just absent. Without
        # ledger_id, resolve_vendor_zoho_contact never sets account_id on the
        # line items and Zoho books the bill wherever it likes - which looks
        # like a successful push and is wrong in the ledger.
        doc["ledger_pending"] = True
    if pan:
        doc["pan"] = pan
    if args.credit_days is not None:
        doc["credit_days"] = args.credit_days
    if args.tds is not None:
        doc["tds_rate"] = round(args.tds, 4)
    if bank:
        doc["bank_details"] = bank

    print(f"VENDOR : {args.name}")
    for key, value in doc.items():
        print(f"   {key:18} {value}")
    if args.credit_days is None:
        print("\n   credit_days is not set, so this vendor is treated as immediate:")
        print("   the due date falls to the next 5th or 20th payment cycle.")

    if not account_doc:
        print("\n   NO LEDGER. Invoices from this vendor will be matched, dated and")
        print("   paid correctly, and the bank check will work. But accepting one")
        print("   sends it to Zoho with no account on the line items, and Zoho")
        print("   books it wherever it chooses - a push that looks successful and")
        print("   is wrong in the books.")
        print("   Set the ledger before accepting any invoice from them:")
        print(f"     python set_vendor_ledger.py \"{args.name}\" --ledger \"<name>\" --apply")
        print("   Find vendors in this state with: { ledger_pending: true }")

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply.")
        return

    await vendors.insert_one(doc)
    print(f"\ninserted into invoice_db.vendors")

    if os.path.exists(LEDGER_WORKBOOK):
        backup = LEDGER_WORKBOOK.replace(".xlsx", f"_backup_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        shutil.copy2(LEDGER_WORKBOOK, backup)
        wb = openpyxl.load_workbook(LEDGER_WORKBOOK)
        wb["Sheet1"].append([
            args.name, doc["ledger_name"], vendor_id,
            doc["zoho_contact_id"], gstin, doc["ledger_id"],
        ])
        wb.save(LEDGER_WORKBOOK)
        print("appended a row to Vendor-Ledgers.xlsx (backed up first)")

    print("\nAdd this vendor to the master sheet too - anything set only here is")
    print("lost the next time vendors are rebuilt from the sheet.")


if __name__ == "__main__":
    asyncio.run(main())
