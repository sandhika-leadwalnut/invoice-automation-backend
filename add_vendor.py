"""
Add a single vendor to Vendor-Ledgers.xlsx (non-destructive) and print the
MongoDB document for invoice_db.vendors.

Unlike test_zoho.py, this does NOT wipe the vendors collection. It only:
  1. Looks the vendor up in Zoho Books by contact_name -> contact_id + gst_no
  2. Resolves the ledger name -> account_id via chart of accounts
  3. Appends one row to Vendor-Ledgers.xlsx (existing rows untouched)
  4. Prints the exact JSON doc to insert into invoice_db.vendors

Usage:
    source venv/bin/activate
    python add_vendor.py "Xperforce Technologies LLP" "Software Development & Technical Manpower Charges"

    # dry run - look up only, don't touch the workbook
    python add_vendor.py "Xperforce Technologies LLP" "Software Development & Technical Manpower Charges" --dry-run
"""

import argparse
import asyncio
import difflib
import json
import os
import uuid

import pandas as pd

from zoho_client import zoho_books_client

HERE = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(HERE, "Vendor-Ledgers.xlsx")

# Zoho contact/account IDs are 19-digit numbers. If pandas infers them as float64
# they silently lose precision (5589375000003097044 -> 5.589375000003097e+18) and
# get written back to the workbook corrupted. Always read them as strings.
ID_COLUMNS = ["Vendor ID", "Zoho Contact ID", "GSTIN", "Ledger ID"]


def read_workbook():
    probe = pd.read_excel(EXCEL_PATH, nrows=0)
    converters = {c: (lambda v: "" if pd.isna(v) else str(v).strip())
                  for c in probe.columns if c in ID_COLUMNS}
    df = pd.read_excel(EXCEL_PATH, converters=converters)
    for c in converters:
        df[c] = df[c].replace("", None)
    return df


async def fetch_all(fetcher):
    out, page = [], 1
    while True:
        batch = await fetcher(page=page, per_page=200)
        if not batch:
            break
        out.extend(batch)
        page += 1
    return out


def norm(s):
    return str(s or "").strip().lower()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendor_name")
    ap.add_argument("ledger_name")
    ap.add_argument("--gstin", default=None, help="Override GSTIN if Zoho has it blank")
    ap.add_argument("--dry-run", action="store_true", help="Look up only; do not modify the workbook")
    ap.add_argument("--print-mongo", action="store_true",
                    help="Also print the invoice_db.vendors document. Off by default; "
                         "this script never writes to MongoDB either way.")
    args = ap.parse_args()

    df = read_workbook()
    vendor_col = next((c for c in df.columns if "vendor" in str(c).lower() and "id" not in str(c).lower()), None)
    ledger_col = next((c for c in df.columns if str(c).strip().lower() == "ledger"), None)
    if not vendor_col:
        raise SystemExit(f"Vendor column not found. Columns: {list(df.columns)}")

    existing = df[df[vendor_col].astype(str).str.strip().str.lower() == norm(args.vendor_name)]
    if not existing.empty:
        print("!! Vendor already present in the workbook:")
        print(existing.to_string(index=False))
        raise SystemExit(1)

    vendors = await fetch_all(zoho_books_client.get_vendors)
    accounts = await fetch_all(zoho_books_client.get_chartofaccounts)
    print(f"Fetched {len(vendors)} vendors, {len(accounts)} accounts from Zoho.")

    by_name = {norm(v.get("contact_name")): v for v in vendors if v.get("contact_name")}
    match = by_name.get(norm(args.vendor_name))
    if not match:
        close = difflib.get_close_matches(norm(args.vendor_name), list(by_name), n=5, cutoff=0.5)
        print(f"\n!! No exact Zoho contact named {args.vendor_name!r}.")
        if close:
            print("   Closest Zoho contacts:")
            for c in close:
                print(f"     - {by_name[c].get('contact_name')}  (contact_id {by_name[c].get('contact_id')})")
        else:
            print("   No similar names found. Create the contact in Zoho Books first.")
        raise SystemExit(1)

    contact_id = match.get("contact_id")
    gstin = args.gstin or match.get("gst_no") or match.get("gst_number") or None

    acct_by_name = {norm(a.get("account_name")): a.get("account_id") for a in accounts if a.get("account_name")}
    ledger_id = acct_by_name.get(norm(args.ledger_name))
    if not ledger_id:
        raise SystemExit(f"!! Ledger {args.ledger_name!r} not found in chart of accounts.")

    vendor_id = str(uuid.uuid4())

    print("\n--- Resolved ---")
    print(f"  Vendor          : {match.get('contact_name')}")
    print(f"  Vendor ID       : {vendor_id}   (newly generated)")
    print(f"  Zoho Contact ID : {contact_id}")
    print(f"  GSTIN           : {gstin or '(none on the Zoho contact)'}")
    print(f"  Ledger          : {args.ledger_name}")
    print(f"  Ledger ID       : {ledger_id}")

    if args.print_mongo:
        doc = {
            "vendor_id": vendor_id,
            "vendor_name": str(args.vendor_name).strip(),
            "ledger_name": str(args.ledger_name).strip(),
            "ledger_id": str(ledger_id),
            "zoho_contact_id": str(contact_id),
            "gstin": str(gstin).strip() if gstin else None,
        }
        print("\n--- invoice_db.vendors document (printed only - nothing was written to Mongo) ---")
        print(json.dumps(doc, indent=2))

    if args.dry_run:
        print("\n(dry run - workbook not modified)")
        return

    new_row = {c: None for c in df.columns}
    new_row[vendor_col] = str(args.vendor_name).strip()
    if ledger_col:
        new_row[ledger_col] = str(args.ledger_name).strip()
    new_row["Vendor ID"] = vendor_id
    new_row["Zoho Contact ID"] = str(contact_id)
    new_row["GSTIN"] = str(gstin).strip() if gstin else None
    new_row["Ledger ID"] = str(ledger_id)

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_excel(EXCEL_PATH, index=False)
    print(f"\nAppended 1 row to {EXCEL_PATH} ({len(df)} rows total).")


if __name__ == "__main__":
    asyncio.run(main())
