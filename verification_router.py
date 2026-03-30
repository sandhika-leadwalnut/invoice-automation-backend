from fastapi import APIRouter, HTTPException, BackgroundTasks, status
from typing import Dict, Any, List
from datetime import datetime
import uuid
from motor.motor_asyncio import AsyncIOMotorClient
import logging

from config import settings
from zoho_client import zoho_books_client
from schemas import IncomingBillPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verification", tags=["Verification"])

# MongoDB connection
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client["invoice_db"]
invoices_col = db["invoices"]

@router.post("/invoice", status_code=status.HTTP_201_CREATED)
async def ingest_invoice(payload: Dict[str, Any]):
    """Ingest a new invoice JSON into MongoDB with 'pending' status."""
    invoice_id = str(uuid.uuid4())
    doc = {
        "_id": invoice_id,
        "invoice_data": payload,
        "status": "pending",
        "edited_data": None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    await invoices_col.insert_one(doc)
    return {"id": invoice_id, "status": "pending"}

@router.get("/invoices/pending")
async def get_pending_invoices():
    """Retrieve all pending invoices for the dashboard."""
    cursor = invoices_col.find({"status": "pending"}).sort("created_at", -1)
    invoices = await cursor.to_list(length=100)
    return invoices

@router.get("/invoice/{id}")
async def get_invoice(id: str):
    """Return the full JSON document of an invoice."""
    doc = await invoices_col.find_one({"_id": id})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return doc

@router.post("/invoice/{id}/action")
async def invoice_action(id: str, action_payload: Dict[str, Any]):
    """
    Handle user action for an invoice: 
    { "action": "accept" | "edit" | "reject", "data": {...} }
    """
    action = action_payload.get("action")
    
    doc = await invoices_col.find_one({"_id": id})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    if action == "reject":
        await invoices_col.update_one(
            {"_id": id}, 
            {"$set": {"status": "rejected", "updated_at": datetime.utcnow()}}
        )
        return {"status": "rejected"}
        
    elif action == "accept":
        await invoices_col.update_one(
            {"_id": id}, 
            {"$set": {"status": "accepted", "updated_at": datetime.utcnow()}}
        )
        
        # Push original to Zoho
        try:
            bill_payload = IncomingBillPayload(**doc["invoice_data"])
            created_bill = await zoho_books_client.create_bill(bill_payload)
            return {"status": "accepted", "zoho_bill": created_bill}
        except Exception as e:
            logger.error(f"Error pushing to zoho on accept: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    elif action == "edit":
        edited_data = action_payload.get("data")
        if not edited_data:
            raise HTTPException(status_code=400, detail="Missing 'data' field for edit action")
            
        await invoices_col.update_one(
            {"_id": id}, 
            {"$set": {
                "status": "edited", 
                "edited_data": edited_data,
                "updated_at": datetime.utcnow()
            }}
        )
        
        # Push edited to Zoho
        try:
            bill_payload = IncomingBillPayload(**edited_data)
            created_bill = await zoho_books_client.create_bill(bill_payload)
            return {"status": "edited", "zoho_bill": created_bill}
        except Exception as e:
            logger.error(f"Error pushing to zoho on edit: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    else:
        raise HTTPException(status_code=400, detail="Invalid action, must be accept, edit, or reject.")
