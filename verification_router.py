from fastapi import APIRouter, HTTPException, BackgroundTasks, status, Query
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid
from motor.motor_asyncio import AsyncIOMotorClient
import logging
import asyncio

from config import settings
from zoho_client import zoho_books_client
from schemas import IncomingBillPayload, EmailMetricsPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verification", tags=["Verification"])

# MongoDB connection
client = AsyncIOMotorClient(settings.mongo_uri)
db = client["invoice_db"]
invoices_col = db["invoices"]
email_metrics_col = db["invoice_email_metrics"]
zoho_push_metrics_col = db["zoho_push_metrics"]

@router.post("/email_metrics", status_code=status.HTTP_200_OK)
async def update_email_metrics(payload: List[EmailMetricsPayload] | EmailMetricsPayload):
    """Update the total count of invoices received by mail."""
    payload_list = payload if isinstance(payload, list) else [payload]
    total_added = sum(item.total_invoices_received for item in payload_list if item.metrics_type == "invoice_email_metrics")
            
    if total_added > 0:
        await email_metrics_col.update_one(
            {"metrics_type": "invoice_email_metrics"},
            {"$inc": {"total_invoices_received": total_added}},
            upsert=True
        )
        
    return {"status": "success", "added": total_added}

@router.post("/invoice", status_code=status.HTTP_201_CREATED)
async def ingest_invoice(payload: Dict[str, Any]):
    """Ingest a new invoice JSON into MongoDB with 'pending' status."""
    invoice_id = str(uuid.uuid4())
    
    # Extract PDF data if present
    base64_pdf = payload.pop("base64_pdf", None)
    pdf_filename = payload.pop("pdf_filename", f"{invoice_id}.pdf")
    pdf_url = None
    
    if base64_pdf:
        import base64
        import os
        pdf_path = f"uploads/{invoice_id}.pdf"
        try:
            with open(pdf_path, "wb") as f:
                f.write(base64.b64decode(base64_pdf))
            # Just store the relative path or construct full URL depending on frontend needs
            pdf_url = f"/uploads/{invoice_id}.pdf"
        except Exception as e:
            logger.error(f"Error saving PDF to local uploads: {e}")

    vendor_exists = False
    gstin = payload.get("vendor_gstin")
    if gstin:
        try:
            vendor = await zoho_books_client.get_vendor_by_gstin(gstin)
            vendor_exists = bool(vendor)
        except Exception as e:
            logger.error(f"Error checking vendor for GSTIN {gstin} at ingestion: {e}")
            vendor_exists = False

    doc = {
        "_id": invoice_id,
        "vendor_name": payload.get("vendor_name"),
        "invoice_data": payload,
        "vendor_exists": vendor_exists,
        "status": "pending",
        "edited_data": None,
        "pdf_url": pdf_url,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    await invoices_col.insert_one(doc)
    return {"id": invoice_id, "status": "pending", "pdf_url": pdf_url}

@router.get("/metrics")
async def get_metrics(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None)
):
    """Retrieve metrics for the dashboard."""
    query = {}
    
    if start_date or end_date:
        date_query = {}
        if start_date:
            try:
                date_query["$gte"] = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            except ValueError:
                pass
        if end_date:
            try:
                date_query["$lte"] = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except ValueError:
                pass
        if date_query:
            query["created_at"] = date_query
            
    if vendor_name:
        query["vendor_name"] = vendor_name
        
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1}
        }}
    ]
    status_counts = await invoices_col.aggregate(pipeline).to_list(None)
    
    total_processed = sum(item["count"] for item in status_counts)
    
    vendor_pipeline = [
        {"$match": query},
        {"$group": {
            "_id": "$vendor_name",
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}}
    ]
    vendor_counts = await invoices_col.aggregate(vendor_pipeline).to_list(None)
    
    timeline_pipeline = [
        {"$match": query},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    timeline_counts = await invoices_col.aggregate(timeline_pipeline).to_list(None)
    
    email_metrics_doc = await email_metrics_col.find_one({"metrics_type": "invoice_email_metrics"})
    total_email_invoices = email_metrics_doc.get("total_invoices_received", 0) if email_metrics_doc else 0

    zoho_push_doc = await zoho_push_metrics_col.find_one({"metrics_type": "zoho_push_metrics"})
    total_zoho_pushed = zoho_push_doc.get("total_pushed", 0) if zoho_push_doc else 0
    
    return {
        "status_distribution": {item["_id"]: item["count"] for item in status_counts},
        "total": total_processed,
        "vendors": [{"vendor": item["_id"] or "Unknown", "count": item["count"]} for item in vendor_counts],
        "timeline": [{"date": item["_id"], "count": item["count"]} for item in timeline_counts],
        "total_email_invoices": total_email_invoices,
        "total_zoho_pushed": total_zoho_pushed
    }


@router.get("/invoices/all")
async def get_all_invoices():
    """Retrieve all invoices for the dashboard."""
    cursor = invoices_col.find({}).sort("created_at", -1)
    invoices = await cursor.to_list(length=1000)
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
        remark = action_payload.get("remark")
        if not remark or not str(remark).strip():
            raise HTTPException(status_code=400, detail="Remark is mandatory for rejection")
            
        await invoices_col.update_one(
            {"_id": id}, 
            {"$set": {"status": "rejected", "remark": str(remark).strip(), "updated_at": datetime.utcnow()}}
        )
        return {"status": "rejected"}
        
    elif action == "accept":
        await invoices_col.update_one(
            {"_id": id}, 
            {"$set": {"status": "accepted", "updated_at": datetime.utcnow()}}
        )
        
        # Push to Zoho using either potentially supplied frontend data or the original source
        payload_data = action_payload.get("data") or doc.get("edited_data") or doc.get("invoice_data", {})
        try:
            bill_payload = IncomingBillPayload(**payload_data)
            created_bill = await zoho_books_client.create_bill(bill_payload)
            
            # Verify bill creation
            verify_status = "verified"
            bill_id = created_bill.get("bill_id")
            if bill_id:
                await zoho_push_metrics_col.update_one(
                    {"metrics_type": "zoho_push_metrics"},
                    {"$inc": {"total_pushed": 1}},
                    upsert=True
                )
                logger.info(f"Triggering comment addition. GDrive link configured: '{settings.gdrive_link}'")
                if settings.gdrive_link:
                    try:
                        comment_text = f"This invoice is available at this path: {settings.gdrive_link}"
                        await zoho_books_client.add_bill_comment(bill_id, comment_text)
                    except Exception as ce:
                        logger.warning(f"Failed to add comment to bill {bill_id}: {ce}")
                        
                verified_bill = await zoho_books_client.get_bill(bill_id)
                if not verified_bill or verified_bill.get("bill_number") != bill_payload.invoice_number:
                    verify_status = "mismatch"
                    logger.warning(f"Verification mismatch for invoice {bill_payload.invoice_number}")
                else:
                    logger.info(f"Verification successful: read request from Zoho matches payload for invoice {bill_payload.invoice_number}")
                    
            await invoices_col.update_one(
                {"_id": id},
                {"$unset": {"invoice_data": "", "edited_data": ""}}
            )
            
            return {"status": "accepted", "zoho_bill": created_bill, "verification_status": verify_status}
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

        return {"status": "edited"}
            
    else:
        raise HTTPException(status_code=400, detail="Invalid action, must be accept, edit, or reject.")
