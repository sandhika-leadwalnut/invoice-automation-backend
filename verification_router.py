from fastapi import APIRouter, HTTPException, BackgroundTasks, status, Query, Response
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid
from motor.motor_asyncio import AsyncIOMotorClient
import logging
import asyncio
import re
import base64
import hashlib

from config import settings
from zoho_client import zoho_books_client
from schemas import IncomingBillPayload, EmailMetricsPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verification", tags=["Verification"])

# An invoice in one of these states already has a corresponding bill in Zoho
# Books. Removing it from the portal would leave the two silently out of step,
# so bulk delete skips them unless the caller explicitly opts in.
ZOHO_SYNCED_STATUSES = ["accepted", "paid"]

# Soft-deleted invoices keep their row but are hidden from every list and
# every dashboard figure. Nothing in this service hard-deletes an invoice.
NOT_DELETED = {"deleted_at": {"$exists": False}}

# MongoDB connection
client = AsyncIOMotorClient(settings.mongo_uri)
db = client["invoice_db"]
invoices_col = db["invoices"]
email_metrics_col = db["invoice_email_metrics"]
zoho_push_metrics_col = db["zoho_push_metrics"]
duplicate_metrics_col = db["duplicate_metrics"]


# --- Duplicate detection ----------------------------------------------------
#
# Duplicates arrive three different ways and each needs its own guard:
#
#   1. The ingestion poller re-reading a Gmail message it already handled.
#      workflow.py only marks a message read once every attachment in it has
#      been processed, so any failure before that line leaves it unread and the
#      next poll, a minute later, repeats the entire message. No vendor did
#      anything; we read one email twice.
#   2. The identical PDF arriving by another route - a forward, a manual
#      upload - where the message id differs but the file does not.
#   3. The vendor genuinely resending, having re-exported the PDF, so the bytes
#      differ even though it is the same bill.
#
# 1 and 2 are certain, so they are dropped without creating a record. 3 is a
# judgement call, so the record is kept and flagged for a human to settle.
#
# Note that an invoice number identifies nothing on its own: CGST Rule 46(b)
# makes it unique only per supplier per financial year, and every supplier
# restarts its series each April.

DUPLICATE_STATUS = "duplicate"

# Certain enough to drop without asking anyone.
CERTAIN_DUPLICATE_REASONS = ("same_email", "identical_file", "same_invoice")

_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
    "%d-%b-%Y", "%d %b %Y", "%d.%m.%Y", "%m/%d/%Y",
)


def _norm_text(value: Any) -> str:
    """Upper-case and drop all whitespace, for comparing extracted text."""
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).upper()


def _norm_invoice_number(value: Any) -> str:
    """
    Normalise an invoice number for comparison.

    Whitespace goes and case is folded, so 'LeadWalnut - 6' and 'LeadWalnut-6'
    match. Hyphens and slashes are deliberately KEPT: Rule 46(b) lets a supplier
    run several parallel series distinguished by exactly those two characters,
    so stripping them would merge 'INV/001' and 'INV-001' into one invoice.
    """
    return _norm_text(value)


def _norm_amount(value: Any) -> Optional[float]:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _parse_invoice_date(value: Any):
    """Parse the invoice date, tolerating the several formats vendors use."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _financial_year(value: Any) -> str:
    """Indian financial year running April to March, e.g. '2026-2027'."""
    parsed = _parse_invoice_date(value)
    if not parsed:
        return "unknown"
    if parsed.month >= 4:
        return f"{parsed.year}-{parsed.year + 1}"
    return f"{parsed.year - 1}-{parsed.year}"


def _vendor_key(payload: Dict[str, Any]) -> str:
    """
    Identify the vendor, strongest identifier first.

    vendor_id leads because the master holds one for all 75 vendors while 42 of
    them have no GSTIN whatsoever - GSTIN simply cannot be the anchor here.
    resolve_vendor_zoho_contact has already run by the time this is called, so a
    mapped vendor always has one. Name comes last because extraction is not
    stable on it: the same PDF has produced both 'ElproDigital by FYE Digit
    Informatics LLP' and 'FYE Digit Informatics LLP'.
    """
    for field in ("vendor_id", "vendor_gstin", "zoho_contact_id"):
        value = _norm_text(payload.get(field))
        if value:
            return f"{field}:{value}"
    name = _norm_text(payload.get("vendor_name"))
    return f"vendor_name:{name}" if name else ""


def _dedupe_key(payload: Dict[str, Any]) -> Optional[str]:
    """
    Vendor + invoice number + financial year.

    This is the 'same bill' family, not proof of a duplicate - a corrected
    invoice re-issued under the same number shares it too. Matching here alone
    means 'worth a look', which is why it flags rather than drops.
    """
    vendor = _vendor_key(payload)
    number = _norm_invoice_number(payload.get("invoice_number"))
    if not vendor or not number:
        return None
    return f"{vendor}|{number}|{_financial_year(payload.get('invoice_date'))}"


def _identity_fingerprint(payload: Dict[str, Any]) -> Optional[str]:
    """
    The four fields Finance uses to call two records the same invoice: vendor,
    invoice number, amount and invoice date. All four must match.

    Returns None when the amount or date is missing, which deliberately means
    the invoice can never be dropped on this rule alone - it falls through to
    the flag-for-review path instead.
    """
    key = _dedupe_key(payload)
    amount = _norm_amount(payload.get("total_amount"))
    parsed_date = _parse_invoice_date(payload.get("invoice_date"))
    if not key or amount is None or parsed_date is None:
        return None
    return f"{key}|{amount}|{parsed_date.isoformat()}"


def _file_sha256(base64_pdf: Optional[str]) -> Optional[str]:
    """Fingerprint the PDF itself, which is immune to extraction mistakes."""
    if not base64_pdf:
        return None
    try:
        return hashlib.sha256(base64.b64decode(base64_pdf)).hexdigest()
    except Exception as e:
        logger.warning(f"Could not hash the attached PDF, skipping the file-level check: {e}")
        return None


async def _find_duplicate(
    gmail_message_id: Optional[str],
    pdf_filename: Optional[str],
    file_sha256: Optional[str],
    fingerprint: Optional[str],
    dedupe_key: Optional[str],
):
    """
    Look for an earlier invoice this one duplicates, cheapest check first.

    Soft-deleted invoices are ignored on purpose: if someone removed an invoice
    and it arrives again, it should come back rather than be silently swallowed.

    Returns (original_document, reason) or (None, None).
    """
    if gmail_message_id:
        existing = await invoices_col.find_one({
            "gmail_message_id": gmail_message_id,
            "pdf_filename": pdf_filename,
            **NOT_DELETED,
        })
        if existing:
            return existing, "same_email"

    if file_sha256:
        existing = await invoices_col.find_one({"file_sha256": file_sha256, **NOT_DELETED})
        if existing:
            return existing, "identical_file"

    if fingerprint:
        existing = await invoices_col.find_one({"identity_fingerprint": fingerprint, **NOT_DELETED})
        if existing:
            return existing, "same_invoice"

    if dedupe_key:
        existing = await invoices_col.find_one({"dedupe_key": dedupe_key, **NOT_DELETED})
        if existing:
            return existing, "similar_invoice"

    return None, None


async def _record_duplicate_hit(original: Dict[str, Any], reason: str, pdf_filename: Optional[str]):
    """
    Note a dropped duplicate against the invoice it duplicates, and add to the
    running total.

    Dropping duplicates widens the gap between 'received by mail' and the number
    of records, so the count is kept separately - otherwise a dropped duplicate
    becomes indistinguishable from a failed extraction, and those need very
    different responses.
    """
    now = datetime.utcnow()
    await invoices_col.update_one(
        {"_id": original["_id"]},
        {
            "$inc": {"duplicate_hits": 1},
            "$set": {"last_duplicate_at": now},
            "$push": {
                "duplicate_log": {
                    "$each": [{"reason": reason, "pdf_filename": pdf_filename, "at": now}],
                    "$slice": -20,
                }
            },
        },
    )
    await duplicate_metrics_col.insert_one({
        "metrics_type": "duplicate_metrics",
        "total_blocked": 1,
        "reason": reason,
        "original_id": original["_id"],
        "created_at": now,
    })


async def ensure_indexes():
    """Indexes backing the duplicate lookups. Safe to call on every startup."""
    try:
        await invoices_col.create_index("gmail_message_id", sparse=True)
        await invoices_col.create_index("file_sha256", sparse=True)
        await invoices_col.create_index("identity_fingerprint", sparse=True)
        await invoices_col.create_index("dedupe_key", sparse=True)
        await invoices_col.create_index("status")
        await invoices_col.create_index("deleted_at", sparse=True)
        logger.info("Duplicate-detection indexes are in place")
    except Exception as e:
        # An index that cannot be built should not stop the service starting;
        # the lookups still work, just more slowly.
        logger.error(f"Could not create duplicate-detection indexes: {e}")

@router.post("/email_metrics", status_code=status.HTTP_200_OK)
async def update_email_metrics(payload: List[EmailMetricsPayload] | EmailMetricsPayload):
    """Update the total count of invoices received by mail."""
    payload_list = payload if isinstance(payload, list) else [payload]
    total_added = sum(item.total_invoices_received for item in payload_list if item.metrics_type == "invoice_email_metrics")
            
    if total_added > 0:
        await email_metrics_col.insert_one({
            "metrics_type": "invoice_email_metrics",
            "total_invoices_received": total_added,
            "created_at": datetime.utcnow()
        })
        
    return {"status": "success", "added": total_added}

async def resolve_vendor_zoho_contact(payload: Dict[str, Any]) -> bool:
    """
    Tries to find the zoho_contact_id for the given payload using vendor_id, GSTIN, or vendor_name.
    Modifies payload in-place to add zoho_contact_id, vendor_id, and ledger_id to line items if found.
    Returns True if vendor is resolved.
    """
    vendors_col = db["vendors"]
    vendor_id = payload.get("vendor_id")
    gstin = payload.get("vendor_gstin")
    vendor_name = payload.get("vendor_name")
    
    def apply_vendor_data(v_doc):
        payload["zoho_contact_id"] = v_doc.get("zoho_contact_id")
        if v_doc.get("vendor_id"):
            payload["vendor_id"] = v_doc.get("vendor_id")
            
        ledger_id = v_doc.get("ledger_id")
        if ledger_id and "line_items" in payload and isinstance(payload["line_items"], list):
            for item in payload["line_items"]:
                if isinstance(item, dict) and not item.get("account_id"):
                    item["account_id"] = str(ledger_id)
                    
    # 1. Match by vendor_id
    if vendor_id:
        try:
            vendor = await vendors_col.find_one({"vendor_id": vendor_id})
            if vendor and vendor.get("zoho_contact_id"):
                apply_vendor_data(vendor)
                return True
        except Exception as e:
            logger.error(f"Error checking vendor for vendor_id {vendor_id}: {e}")
            
    # 2. Match by GSTIN in DB
    if gstin:
        try:
            vendor = await vendors_col.find_one({"gstin": gstin.strip()})
            if vendor and vendor.get("zoho_contact_id"):
                apply_vendor_data(vendor)
                return True
        except Exception as e:
            logger.error(f"Error checking vendor for GSTIN {gstin} in DB: {e}")
            
    # 3. Match by vendor_name in DB
    if vendor_name:
        try:
            name_regex = re.compile(f"^{re.escape(vendor_name.strip())}$", re.IGNORECASE)
            vendor = await vendors_col.find_one({"vendor_name": name_regex})
            if vendor and vendor.get("zoho_contact_id"):
                apply_vendor_data(vendor)
                return True
        except Exception as e:
            logger.error(f"Error checking vendor for name {vendor_name} in DB: {e}")

    # 4. Fallback to Zoho API by GSTIN
    if gstin:
        try:
            vendor = await zoho_books_client.get_vendor_by_gstin(gstin)
            if vendor and vendor.get("contact_id"):
                payload["zoho_contact_id"] = vendor.get("contact_id")
                return True
        except Exception as e:
            logger.error(f"Error checking vendor for GSTIN {gstin} at Zoho fallback: {e}")
            
    return False

@router.post("/invoice", status_code=status.HTTP_201_CREATED)
async def ingest_invoice(payload: Dict[str, Any], response: Response):
    """
    Ingest a new invoice JSON into MongoDB with 'pending' status.

    Before anything is stored the payload is checked against what is already
    here. A certain duplicate is dropped outright and noted against the invoice
    it repeats; a probable one is stored but flagged for review rather than
    discarded, because silently losing a real invoice is the worse failure.
    """
    invoice_id = str(uuid.uuid4())

    # Extract PDF data if present
    base64_pdf = payload.pop("base64_pdf", None)
    pdf_filename = payload.pop("pdf_filename", f"{invoice_id}.pdf")
    gmail_message_id = payload.pop("gmail_message_id", None)
    pdf_url = None

    # Fingerprint the file before anything else. This is the one check that does
    # not care what Unstract managed to read off the page.
    file_sha256 = _file_sha256(base64_pdf)

    # Map items_table to line_items if Unstract populated items_table instead
    if "items_table" in payload and isinstance(payload["items_table"], list) and len(payload["items_table"]) > 0:
        if not payload.get("line_items") or len(payload.get("line_items", [])) == 0:
            payload["line_items"] = payload.pop("items_table")

    if "line_items" in payload and isinstance(payload["line_items"], list):
        for item in payload["line_items"]:
            qty = item.get("quantity")
            try:
                if qty is None or qty == "" or float(qty) == 0:
                    item["quantity"] = 1
            except (ValueError, TypeError):
                item["quantity"] = 1
                
            unit_price = item.get("unit_price")
            amount = item.get("amount")
            try:
                if (unit_price is None or unit_price == "" or float(unit_price) == 0) and amount is not None and amount != "":
                    item["unit_price"] = float(amount) / float(item["quantity"])
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    # The vendor has to be resolved before the duplicate keys are built, because
    # vendor_id is the anchor and it only exists once this has run.
    vendor_exists = await resolve_vendor_zoho_contact(payload)

    dedupe_key = _dedupe_key(payload)
    fingerprint = _identity_fingerprint(payload)

    original, reason = await _find_duplicate(
        gmail_message_id=gmail_message_id,
        pdf_filename=pdf_filename,
        file_sha256=file_sha256,
        fingerprint=fingerprint,
        dedupe_key=dedupe_key,
    )

    if original is not None and reason in CERTAIN_DUPLICATE_REASONS:
        # Never should have been picked up in the first place. No record, no PDF
        # written - just a note against the original so the count is traceable.
        await _record_duplicate_hit(original, reason, pdf_filename)
        logger.info(
            f"Dropped duplicate ({reason}) of invoice {original['_id']} "
            f"- file {pdf_filename}"
        )
        response.status_code = status.HTTP_200_OK
        return {
            "id": original["_id"],
            "status": "duplicate_ignored",
            "reason": reason,
            "duplicate_of": original["_id"],
        }

    # Only worth writing the file to disk once we know we are keeping the record.
    if base64_pdf:
        import os
        upload_dir = settings.upload_dir
        os.makedirs(upload_dir, exist_ok=True)
        pdf_path = os.path.join(upload_dir, f"{invoice_id}.pdf")
        try:
            with open(pdf_path, "wb") as f:
                f.write(base64.b64decode(base64_pdf))
            logger.info(f"Saved PDF to full path: {pdf_path}")
            # Just store the relative path or construct full URL depending on frontend needs
            pdf_url = f"/uploads/{invoice_id}.pdf"
        except Exception as e:
            logger.error(f"Error saving PDF to local uploads at {pdf_path}: {e}")

    # A dedupe_key match on its own means same vendor, same invoice number, same
    # financial year - but a different amount or date. That is either a resend
    # we could not confirm or a corrected invoice re-issued under the old
    # number, and only a person can tell those apart.
    is_probable_duplicate = original is not None and reason == "similar_invoice"

    doc = {
        "_id": invoice_id,
        "vendor_name": payload.get("vendor_name"),
        "invoice_data": payload,
        "vendor_exists": vendor_exists,
        "status": DUPLICATE_STATUS if is_probable_duplicate else "pending",
        "edited_data": None,
        "pdf_url": pdf_url,
        # pdf_filename was previously popped off the payload and discarded, which
        # left no way to tie an invoice back to its file in Google Drive.
        # drive_file_id is the reliable key; the filename is kept as a fallback.
        "pdf_filename": pdf_filename,
        "drive_file_id": payload.get("drive_file_id"),
        # Duplicate-detection keys, stored so later arrivals can be matched
        # against this record without recomputing anything.
        "gmail_message_id": gmail_message_id,
        "file_sha256": file_sha256,
        "dedupe_key": dedupe_key,
        "identity_fingerprint": fingerprint,
        "duplicate_of": original["_id"] if is_probable_duplicate else None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    await invoices_col.insert_one(doc)

    if is_probable_duplicate:
        logger.info(
            f"Flagged invoice {invoice_id} as a probable duplicate of "
            f"{original['_id']} - same vendor and number, different amount or date"
        )

    return {"id": invoice_id, "status": doc["status"], "pdf_url": pdf_url}

@router.get("/metrics")
async def get_metrics(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None)
):
    """Retrieve metrics for the dashboard."""
    # Soft-deleted invoices are excluded from every figure on the dashboard.
    query = dict(NOT_DELETED)

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
    
    email_query = {"metrics_type": "invoice_email_metrics"}
    if "created_at" in query:
        email_query["created_at"] = query["created_at"]
        
    email_pipeline = [
        {"$match": email_query},
        {"$group": {
            "_id": None,
            "total": {"$sum": "$total_invoices_received"}
        }}
    ]
    email_metrics_res = await email_metrics_col.aggregate(email_pipeline).to_list(None)
    total_email_invoices = email_metrics_res[0]["total"] if email_metrics_res else 0

    zoho_query = {"metrics_type": "zoho_push_metrics"}
    if "created_at" in query:
        zoho_query["created_at"] = query["created_at"]
    if "vendor_name" in query:
        zoho_query["vendor_name"] = query["vendor_name"]

    zoho_pipeline = [
        {"$match": zoho_query},
        {"$group": {
            "_id": None,
            "total": {"$sum": "$total_pushed"}
        }}
    ]
    zoho_metrics_res = await zoho_push_metrics_col.aggregate(zoho_pipeline).to_list(None)
    total_zoho_pushed = zoho_metrics_res[0]["total"] if zoho_metrics_res else 0

    # Duplicates that were dropped before a record was ever created. Counted
    # separately so a blocked duplicate is never mistaken for a lost invoice:
    # received_by_mail should reconcile as records + duplicates + failures.
    duplicate_query = {"metrics_type": "duplicate_metrics"}
    if "created_at" in query:
        duplicate_query["created_at"] = query["created_at"]

    duplicate_pipeline = [
        {"$match": duplicate_query},
        {"$group": {
            "_id": None,
            "total": {"$sum": "$total_blocked"}
        }}
    ]
    duplicate_metrics_res = await duplicate_metrics_col.aggregate(duplicate_pipeline).to_list(None)
    total_duplicates_blocked = duplicate_metrics_res[0]["total"] if duplicate_metrics_res else 0

    return {
        "status_distribution": {item["_id"]: item["count"] for item in status_counts},
        "total": total_processed,
        "vendors": [{"vendor": item["_id"] or "Unknown", "count": item["count"]} for item in vendor_counts],
        "timeline": [{"date": item["_id"], "count": item["count"]} for item in timeline_counts],
        "total_email_invoices": total_email_invoices,
        "total_zoho_pushed": total_zoho_pushed,
        "total_duplicates_blocked": total_duplicates_blocked
    }


@router.get("/invoices/all")
async def get_all_invoices():
    """Retrieve all invoices for the dashboard, excluding soft-deleted ones."""
    cursor = invoices_col.find(dict(NOT_DELETED)).sort("created_at", -1)
    invoices = await cursor.to_list(length=1000)
    return invoices

@router.get("/invoice/{id}")
async def get_invoice(id: str):
    """Return the full JSON document of an invoice."""
    doc = await invoices_col.find_one({"_id": id})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    # Dynamically apply latest vendor/ledger mappings to the payload before returning to UI
    payload = doc.get("edited_data") or doc.get("invoice_data")
    if payload:
        await resolve_vendor_zoho_contact(payload)
        
    return doc

@router.get("/vendor/lookup")
async def vendor_lookup(
    gstin: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
):
    """
    Read-only canonical vendor lookup.

    Used by the ingestion service to decide which Google Drive folder an invoice
    PDF belongs in. Returns the vendor_name exactly as stored in invoice_db.vendors
    so folder names stay consistent, instead of relying on the raw OCR spelling
    which varies between documents.

    Never writes. Returns matched=False rather than erroring when nothing is found.
    """
    vendors_col = db["vendors"]
    doc = None

    if vendor_id:
        doc = await vendors_col.find_one({"vendor_id": vendor_id.strip()})
    if not doc and gstin:
        doc = await vendors_col.find_one({"gstin": gstin.strip()})
    if not doc and vendor_name:
        name_regex = re.compile(f"^{re.escape(vendor_name.strip())}$", re.IGNORECASE)
        doc = await vendors_col.find_one({"vendor_name": name_regex})

    if not doc:
        return {"matched": False, "vendor_name": None, "zoho_contact_id": None}

    return {
        "matched": True,
        "vendor_name": doc.get("vendor_name"),
        "zoho_contact_id": doc.get("zoho_contact_id"),
        "ledger_id": doc.get("ledger_id"),
    }


@router.get("/vendors/mapped")
async def vendors_mapped():
    """
    Read-only list of every vendor in invoice_db.vendors.

    Used by the Drive migration so it can match filenames against all mapped
    vendors, not just the ones that happen to appear on an existing invoice.
    """
    cursor = db["vendors"].find(
        {}, {"_id": 0, "vendor_name": 1, "gstin": 1, "vendor_id": 1}
    )
    vendors = await cursor.to_list(length=5000)
    return [v for v in vendors if v.get("vendor_name")]


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
        # Push to Zoho using either potentially supplied frontend data or the original source
        payload_data = action_payload.get("data") or doc.get("edited_data") or doc.get("invoice_data", {})
        
        # Fallback to map items_table to line_items if not done yet
        if "items_table" in payload_data and isinstance(payload_data["items_table"], list) and len(payload_data["items_table"]) > 0:
            if not payload_data.get("line_items") or len(payload_data.get("line_items", [])) == 0:
                payload_data["line_items"] = payload_data.pop("items_table")
                
        if "line_items" in payload_data and isinstance(payload_data["line_items"], list):
            for item in payload_data["line_items"]:
                qty = item.get("quantity")
                try:
                    if qty is None or qty == "" or float(qty) == 0:
                        item["quantity"] = 1
                except (ValueError, TypeError):
                    item["quantity"] = 1
                    
                unit_price = item.get("unit_price")
                amount = item.get("amount")
                try:
                    if (unit_price is None or unit_price == "" or float(unit_price) == 0) and amount is not None and amount != "":
                        item["unit_price"] = float(amount) / float(item["quantity"])
                except (ValueError, TypeError, ZeroDivisionError):
                    pass
                
        try:
            await resolve_vendor_zoho_contact(payload_data)
                    
            bill_payload = IncomingBillPayload(**payload_data)
            created_bill = await zoho_books_client.create_bill(bill_payload)
            
            # Verify bill creation
            verify_status = "verified"
            bill_id = created_bill.get("bill_id")
            if bill_id:
                vendor_name_to_save = payload_data.get("vendor_name") or doc.get("vendor_name")
                await zoho_push_metrics_col.insert_one({
                    "metrics_type": "zoho_push_metrics",
                    "total_pushed": 1,
                    "vendor_name": vendor_name_to_save,
                    "created_at": datetime.utcnow()
                })
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
                {
                    "$set": {"status": "accepted", "updated_at": datetime.utcnow()},
                    "$unset": {"invoice_data": "", "edited_data": ""}
                }
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

@router.delete("/invoice/{id}")
async def delete_invoice(id: str):
    """
    Soft-delete an invoice by ID.

    The row is kept and stamped with deleted_at rather than removed, so an
    accidental delete can be undone and the audit trail survives. Lists and
    dashboard figures filter these out.
    """
    now = datetime.utcnow()
    result = await invoices_col.update_one(
        {"_id": id, **NOT_DELETED},
        {"$set": {"deleted_at": now, "updated_at": now}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"status": "deleted"}


@router.post("/invoices/bulk-delete")
async def bulk_delete_invoices(payload: Dict[str, Any]):
    """
    Soft-delete several invoices at once.

    { "ids": [...], "include_synced": false }

    Invoices that already reached Zoho (accepted / paid) are skipped unless
    include_synced is explicitly true, and the count of skipped rows comes back
    so the caller can tell the user what was left alone and why.
    """
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="'ids' must be a non-empty list")

    query: Dict[str, Any] = {"_id": {"$in": ids}, **NOT_DELETED}
    if not payload.get("include_synced"):
        query["status"] = {"$nin": ZOHO_SYNCED_STATUSES}

    now = datetime.utcnow()
    result = await invoices_col.update_many(
        query,
        {"$set": {"deleted_at": now, "updated_at": now}}
    )

    return {
        "status": "deleted",
        "deleted": result.modified_count,
        "skipped": len(ids) - result.modified_count,
    }


@router.post("/invoices/bulk-mark-paid")
async def bulk_mark_paid(payload: Dict[str, Any]):
    """
    Mark several accepted invoices as paid.

    { "ids": [...] }

    Only invoices currently in the 'accepted' state move to 'paid' — anything
    still pending, edited, rejected or already paid is left untouched and
    reported back as skipped.
    """
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="'ids' must be a non-empty list")

    now = datetime.utcnow()
    result = await invoices_col.update_many(
        {"_id": {"$in": ids}, "status": "accepted", **NOT_DELETED},
        {"$set": {"status": "paid", "paid_at": now, "updated_at": now}}
    )

    return {
        "status": "paid",
        "marked": result.modified_count,
        "skipped": len(ids) - result.modified_count,
    }

