import asyncio
import pandas as pd
from zoho_client import zoho_books_client
from motor.motor_asyncio import AsyncIOMotorClient
from config import settings
import uuid

async def main():
    try:
        df = pd.read_excel("../Vendor-Ledgers.xlsx")
    except Exception as e:
        print(f"Error reading excel: {e}")
        return
        
    # Check if 'Vendor' is in columns. If not, maybe it's in a row.
    vendor_col = next((c for c in df.columns if "vendor" in str(c).lower()), None)
    if not vendor_col:
        vendor_row_index = -1
        for i, row in df.iterrows():
            if any(str(val).strip().lower() == 'vendor' for val in row if pd.notna(val)):
                vendor_row_index = i
                break
                
        if vendor_row_index != -1:
            df = pd.read_excel("../Vendor-Ledgers.xlsx", header=vendor_row_index + 1)
            vendor_col = next((c for c in df.columns if "vendor" in str(c).lower()), None)
            
    ledger_col = next((c for c in df.columns if "ledger" in str(c).lower()), None)
    
    if not vendor_col:
        print("Vendor column not found! Columns:", df.columns)
        return
        
    all_vendors = []
    page = 1
    while True:
        vendors = await zoho_books_client.get_vendors(page=page, per_page=200)
        if not vendors:
            break
        all_vendors.extend(vendors)
        page += 1
        
    print(f"Fetched {len(all_vendors)} vendors from Zoho.")
    
    # Fetch chart of accounts
    all_accounts = []
    page = 1
    while True:
        accounts = await zoho_books_client.get_chartofaccounts(page=page, per_page=200)
        if not accounts:
            break
        all_accounts.extend(accounts)
        page += 1
        
    print(f"Fetched {len(all_accounts)} accounts from Zoho.")
    
    account_name_to_id = {
        a.get("account_name").lower().strip(): a.get("account_id") 
        for a in all_accounts if a.get("account_name")
    }
    
    # Map name -> {contact_id, gst_no}
    zoho_name_to_data = {
        v.get("contact_name").lower().strip(): {
            "contact_id": v.get("contact_id"),
            "gst_no": v.get("gst_no") or v.get("gst_number") or ""
        } for v in all_vendors if v.get("contact_name")
    }
    
    vendor_ids = []
    zoho_contact_ids = []
    gstin_list = []
    ledger_id_list = []
    
    for index, row in df.iterrows():
        vendor_name = str(row[vendor_col]).strip()
        if pd.isna(row[vendor_col]) or vendor_name == 'nan':
            vendor_ids.append(None)
            zoho_contact_ids.append(None)
            gstin_list.append(None)
            continue
            
        # Keep existing vendor_id if present, else new one
        v_id = row.get("Vendor ID") if "Vendor ID" in df.columns and pd.notna(row.get("Vendor ID")) else str(uuid.uuid4())
        vendor_ids.append(v_id)
        
        zoho_data = zoho_name_to_data.get(vendor_name.lower(), {})
        contact_id = zoho_data.get("contact_id")
        gstin = zoho_data.get("gst_no", "")
        
        # If user already put a GSTIN in the sheet, keep it, else use Zoho's
        existing_gstin = row.get("GSTIN") if "GSTIN" in df.columns and pd.notna(row.get("GSTIN")) else None
        final_gstin = existing_gstin if existing_gstin and str(existing_gstin).strip() != 'nan' else gstin
        
        ledger_name = str(row[ledger_col]).strip() if ledger_col and pd.notna(row[ledger_col]) else ""
        ledger_id = account_name_to_id.get(ledger_name.lower())
        
        zoho_contact_ids.append(contact_id)
        gstin_list.append(final_gstin)
        ledger_id_list.append(ledger_id)

    df["Vendor ID"] = vendor_ids
    df["Zoho Contact ID"] = zoho_contact_ids
    df["GSTIN"] = gstin_list
    df["Ledger ID"] = ledger_id_list
    
    df.to_excel("../Vendor-Ledgers.xlsx", index=False)
    print("Saved updated Excel to Vendor-Ledgers.xlsx")
    
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client["invoice_db"]
    vendors_col = db["vendors"]
    
    await vendors_col.delete_many({})
    
    records = []
    for index, row in df.iterrows():
        if pd.isna(row[vendor_col]) or str(row[vendor_col]).strip() == 'nan':
            continue
        records.append({
            "vendor_id": row["Vendor ID"],
            "vendor_name": str(row[vendor_col]).strip(),
            "ledger_name": str(row[ledger_col]).strip() if ledger_col and not pd.isna(row[ledger_col]) else None,
            "ledger_id": str(row["Ledger ID"]).strip() if "Ledger ID" in df.columns and pd.notna(row["Ledger ID"]) else None,
            "zoho_contact_id": row["Zoho Contact ID"],
            "gstin": str(row["GSTIN"]).strip() if pd.notna(row["GSTIN"]) and str(row["GSTIN"]).strip() and str(row["GSTIN"]).strip() != 'nan' else None
        })
        
    if records:
        await vendors_col.insert_many(records)
        print(f"Inserted {len(records)} vendors into MongoDB.")
    else:
        print("No valid vendor records to insert.")

if __name__ == "__main__":
    asyncio.run(main())
