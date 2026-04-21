import asyncio
from verification_router import ingest_invoice, get_pending_invoices, invoices_col
import uuid

async def test_all():
    # Insert a dummy invoice with GSTIN
    # n8n payload format simulation
    payload = {
        "vendor_name": "Test Vendor",
        "vendor_gstin": "29AAGCN0349C1ZN", # A legit one from test_get.py
        "invoice_number": f"INV-{uuid.uuid4().hex[:6]}",
        "invoice_date": "2026-04-21"
    }

    print("Submitting invoice...")
    response = await ingest_invoice(payload)
    print("Ingest response:", response)

    print("Fetching pending invoices...")
    pending = await get_pending_invoices()
    
    # find our recently submitted invoice
    for inv in pending:
        if inv["_id"] == response["id"]:
            print(f"Vendor Exists Flag for {response['id']}: {inv.get('vendor_exists')}")
            break

    # Clean up
    await invoices_col.delete_one({"_id": response["id"]})

if __name__ == "__main__":
    asyncio.run(test_all())
