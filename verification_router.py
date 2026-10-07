from fastapi import APIRouter, HTTPException, BackgroundTasks, status, Query, Response
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
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


# Extraction normalises dates to yyyy-mm-dd using US day/month order, so an
# Indian invoice dated 01/10/2026 arrives as 2026-01-10. Only dates on the 1st
# to the 12th can be misread this way - past the 12th there is no valid US
# reading - which is why the damage looks random rather than total.
FLIP_MUST_BEAT_BY_DAYS = 30   # how much closer the swap has to be before trusting it
FLIP_PLAUSIBLE_WINDOW = 60    # how near the arrival date the swap must land


def _corrected_invoice_date(raw: Any, arrived=None):
    """
    Spot a day/month swap and return the corrected date, or None.

    Only corrects when the swap is provably better: clearly closer to the day
    the invoice reached us, and not in the future. A genuinely old invoice -
    one sent months late - fails those tests and is left exactly as it is,
    because silently redating a real invoice is worse than leaving a wrong one
    visible.
    """
    parsed = _parse_invoice_date(raw)
    if not parsed or parsed.day > 12:
        return None

    arrived = arrived or datetime.utcnow().date()
    try:
        swapped = parsed.replace(month=parsed.day, day=parsed.month)
    except ValueError:
        return None

    current_gap = abs((arrived - parsed).days)
    swapped_gap = abs((arrived - swapped).days)

    if (swapped_gap + FLIP_MUST_BEAT_BY_DAYS < current_gap
            and swapped <= arrived + timedelta(days=1)
            and swapped_gap <= FLIP_PLAUSIBLE_WINDOW):
        return swapped
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


# --- Expected payment date -------------------------------------------------
#
# Whether a credit period counts calendar days or working days.
#
# Commercially, "Net 30" means 30 CALENDAR days - that is what a vendor agreed
# to. Counting only working days stretches 30 days into roughly 42 calendar
# days, which means paying every vendor later than the contract says. The
# reimbursement service counts working days because that is an internal
# processing SLA, not a contractual term; the two are not the same thing.
#
# Finance has not settled this yet, so it is a single switch rather than an
# assumption buried in the arithmetic. Change the value, restart, done.
CREDIT_PERIOD_BASIS = "business"   # "business" | "calendar"

_WEEKEND = (5, 6)  # Saturday, Sunday


def _add_credit_period(start, days: int, basis: str = None):
    """
    Add a credit period to a date.

    business : count only working days.
    calendar : add calendar days, then move off a weekend to the next working
               day so the date is one a bank transfer can actually happen on.

    Public holidays are NOT handled - there is no holiday calendar in this
    service yet, so a due date can still land on one.
    """
    basis = basis or CREDIT_PERIOD_BASIS
    if basis == "business":
        current, counted = start, 0
        while counted < days:
            current += timedelta(days=1)
            if current.weekday() not in _WEEKEND:
                counted += 1
        return current

    due = start + timedelta(days=days)
    while due.weekday() in _WEEKEND:
        due += timedelta(days=1)
    return due


# Payments go out twice a month, on the 5th and the 20th.
PAYMENT_CYCLE_DAYS = (5, 20)

# An invoice dated just after a run still joins that run. Finance holds the
# window open for three days rather than pushing a vendor a fortnight down the
# line over a day or two, so an invoice dated the 6th is paid on the 5th.
PAYMENT_CYCLE_GRACE_DAYS = 3


def _payment_cycle_dates(around: date):
    """Every 5th and 20th from the month before `around` to the month after."""
    dates = []
    for offset in (-1, 0, 1):
        index = around.month - 1 + offset
        year = around.year + index // 12
        month = index % 12 + 1
        for day in PAYMENT_CYCLE_DAYS:
            dates.append(date(year, month, day))
    return sorted(dates)


def _next_payment_cycle(invoice_date: date) -> Optional[date]:
    """
    The payment run an invoice with no agreed credit period belongs to.

    Dated within the grace window after a run, it joins that run - the 6th is
    paid on the 5th. Later than that, it waits for the next one - the 25th is
    paid on the 5th of the following month.

    The cycle date is used as-is even when it falls on a weekend, matching what
    the reimbursement portal already does, so both systems name the same day.
    """
    cycles = _payment_cycle_dates(invoice_date)

    passed = [c for c in cycles if c <= invoice_date]
    if passed and (invoice_date - passed[-1]).days <= PAYMENT_CYCLE_GRACE_DAYS:
        return passed[-1]

    upcoming = [c for c in cycles if c > invoice_date]
    return upcoming[0] if upcoming else None


def _usable_vendor_gstin(value: Any) -> Optional[str]:
    """
    A vendor GSTIN worth matching on.

    Returns None for our own GSTIN. Extraction occasionally reads the buyer's
    GSTIN into vendor_gstin, and a vendor record that happens to carry that same
    value would then swallow every such invoice - handing it that vendor's
    credit period and, on a payment sheet, that vendor's bank account. One such
    record has already done exactly that.
    """
    gstin = str(value or "").strip()
    if not gstin:
        return None
    if _norm_text(gstin) == _norm_text(settings.company_gstin):
        logger.warning(
            "Invoice carries our own GSTIN as the vendor's; ignoring it for "
            "vendor matching - extraction has most likely read the buyer's."
        )
        return None
    return gstin


async def _find_vendor_doc(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fetch the vendor master record, by the same ladder used to resolve Zoho."""
    vendors_col = db["vendors"]
    vendor_id = payload.get("vendor_id")
    if vendor_id:
        vendor = await vendors_col.find_one({"vendor_id": vendor_id})
        if vendor:
            return vendor
    gstin = _usable_vendor_gstin(payload.get("vendor_gstin"))
    if gstin:
        vendor = await vendors_col.find_one({"gstin": gstin})
        if vendor:
            return vendor
    return None


async def _compute_expected_payment_date(payload: Dict[str, Any]) -> Optional[datetime]:
    """
    Due date = the invoice's own date plus that vendor's credit period.

    Deliberately taken from invoice_date, never from when the email arrived or
    when we happened to process it - the vendor's clock starts at their invoice.

    Returns None when the vendor has no credit_days on record, or the invoice
    date cannot be read. An empty cell Finance can see and chase is far better
    than a plausible-looking date derived from a default nobody agreed to.
    """
    invoice_date = _parse_invoice_date(payload.get("invoice_date"))
    if not invoice_date:
        logger.info("No readable invoice date; expected payment date left unset")
        return None

    vendor = await _find_vendor_doc(payload)
    raw_credit_days = vendor.get("credit_days") if vendor else None

    credit_days = None
    if raw_credit_days is not None:
        try:
            credit_days = int(raw_credit_days)
        except (TypeError, ValueError):
            logger.warning(
                f"credit_days on vendor {payload.get('vendor_name')!r} is not a number; "
                f"treating it as immediate"
            )

    if not credit_days:
        # No agreed credit period - or zero, which means the same thing - so the
        # invoice is due on the next payment run rather than on a date of its own.
        due = _next_payment_cycle(invoice_date)
        logger.info(
            f"No credit period for vendor {payload.get('vendor_name')!r}; "
            f"assigned to the {due} payment run"
        )
    else:
        due = _add_credit_period(invoice_date, credit_days)

    if not due:
        return None

    # Stored as a BSON date so it sorts and filters properly.
    return datetime(due.year, due.month, due.day)


# --- Bank detail checking -------------------------------------------------
#
# A vendor's bank account changing is the single most abused route into a
# payments process: a convincing invoice with a new account number, and the
# money goes somewhere else. So the account an invoice asks to be paid into is
# compared against the one on the vendor master, and any difference is flagged
# for a person.
#
# Two rules this must never break:
#   - a mismatch NEVER updates the stored details. Letting an invoice rewrite
#     the vendor master is exactly the attack.
#   - the payment sheet always uses the STORED details, never the invoice's.
#
# Extraction field names vary, so several spellings are accepted.
BANK_ACCOUNT_FIELDS = (
    "vendor_bank_account", "bank_account", "account_number", "vendor_account_number",
    "bank_account_number", "account_no", "vendor_account",
)
BANK_IFSC_FIELDS = (
    "vendor_bank_ifsc", "bank_ifsc", "ifsc", "ifsc_code", "vendor_ifsc",
)
BANK_NAME_FIELDS = (
    "vendor_bank_name", "bank_name", "bank", "vendor_bank",
)


def _first_present(payload: Dict[str, Any], fields) -> Optional[str]:
    for field in fields:
        value = payload.get(field)
        if value in (None, "", "-"):
            continue
        if isinstance(value, float) and value.is_integer():
            # Extraction sometimes declares the account number as a number
            # rather than text, in which case it arrives as a float. str() on
            # a large float gives scientific notation, which is not an account
            # number and would never match. Render the digits instead.
            return str(int(value))
        return str(value).strip()
    return None


def _norm_account(value: Any) -> str:
    """Account numbers vary by spacing and punctuation, never by digits."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


# Bank details are read off a PDF, and OCR confuses letters with the digits
# they resemble - KARB0000107 comes back as KARBO000107, with a letter O in
# place of the reserved zero.
#
# Correcting these is only safe where a letter cannot legitimately appear,
# because a correction that could apply to real data would hide exactly the
# change this check exists to catch. Two places qualify:
#
#   - position five of an IFSC, which the RBI reserves as '0'. Any other
#     character there is a misreading, with no exceptions.
#   - an account number whose stored counterpart is entirely numeric, as every
#     Indian bank account number is. A letter in the extracted value cannot be
#     a different account; it can only be a misread digit.
#
# Outside those two cases the comparison stays strict.
OCR_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "Z": "2", "S": "5", "G": "6", "B": "8",
})


def _norm_ifsc(value: Any) -> str:
    """
    Canonical IFSC: four letters, a reserved '0', then six alphanumerics.

    Only position five is corrected. The last six characters may legitimately
    contain letters, so a letter there is left exactly as read.
    """
    code = _norm_account(value)
    if len(code) == 11 and code[4] != "0":
        code = code[:4] + "0" + code[5:]
    return code


def _accounts_match(invoice_value: Any, stored_value: Any) -> bool:
    """
    True when the invoice asks to be paid into the account on record.

    Falls back to OCR folding only when the stored account is numeric and the
    two are the same length - a genuine change of account would not survive
    that test, because it would differ in digits, not in letter shapes.
    """
    seen = _norm_account(invoice_value)
    record = _norm_account(stored_value)
    if seen == record:
        return True
    if record.isdigit() and len(seen) == len(record):
        return seen.translate(OCR_DIGIT_LOOKALIKES) == record
    return False


async def _check_bank_details(payload: Dict[str, Any], vendor: Optional[Dict[str, Any]]):
    """
    Compare the bank details on an invoice against the vendor master.

    Returns a dict to store on the invoice, or None when there is nothing to
    say. Deliberately silent when the invoice carries no bank details - most do
    not, and a warning on every one of them would train people to ignore it.
    """
    invoice_account = _first_present(payload, BANK_ACCOUNT_FIELDS)
    invoice_ifsc = _first_present(payload, BANK_IFSC_FIELDS)
    if not (invoice_account or invoice_ifsc):
        return None

    stored = (vendor or {}).get("bank_details") or {}
    if not stored:
        # Nothing to compare against. Worth recording so the first account seen
        # for a vendor can be checked once, rather than assumed.
        return {
            "status": "unverified",
            "reason": "no bank details on the vendor master to compare against",
            "invoice": {
                "account_number": invoice_account,
                "ifsc": invoice_ifsc,
                "bank": _first_present(payload, BANK_NAME_FIELDS),
            },
            "checked_at": datetime.utcnow(),
        }

    # Which vendor record this was compared against. Without it, a flag caused
    # by resolving to the wrong vendor looks identical to a real account
    # change, and telling them apart means querying the database by hand.
    matched_vendor = {
        "name": (vendor or {}).get("vendor_name"),
        "id": (vendor or {}).get("_id"),
    }

    differences = []
    ocr_corrected = []

    if invoice_account and not _accounts_match(invoice_account, stored.get("account_number")):
        differences.append({
            "field": "account_number",
            "on_invoice": invoice_account,
            "on_record": stored.get("account_number"),
        })
    elif invoice_account and _norm_account(invoice_account) != _norm_account(stored.get("account_number")):
        ocr_corrected.append("account_number")

    if invoice_ifsc and _norm_ifsc(invoice_ifsc) != _norm_ifsc(stored.get("ifsc")):
        differences.append({
            "field": "ifsc",
            "on_invoice": invoice_ifsc,
            "on_record": stored.get("ifsc"),
        })
    elif invoice_ifsc and _norm_account(invoice_ifsc) != _norm_account(stored.get("ifsc")):
        ocr_corrected.append("ifsc")

    if not differences:
        if ocr_corrected:
            # Matched, but only after correcting a misread character. Worth
            # recording: a vendor whose details need correcting every month
            # points at an extraction problem to fix at the source.
            logger.info(
                "Bank details on invoice %s from %r matched after OCR correction: %s",
                payload.get("invoice_number"), payload.get("vendor_name"),
                ", ".join(ocr_corrected),
            )
        return {
            "status": "matched",
            "matched_vendor": matched_vendor,
            "ocr_corrected": ocr_corrected or None,
            "checked_at": datetime.utcnow(),
        }

    logger.warning(
        "Bank details on invoice %s from %r do not match the vendor master %r: %s",
        payload.get("invoice_number"), payload.get("vendor_name"),
        matched_vendor["name"],
        ", ".join(d["field"] for d in differences),
    )
    return {
        "status": "mismatch",
        "differences": differences,
        "matched_vendor": matched_vendor,
        "invoice": {
            "account_number": invoice_account,
            "ifsc": invoice_ifsc,
            "bank": _first_present(payload, BANK_NAME_FIELDS),
        },
        "checked_at": datetime.utcnow(),
    }


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
    # Never our own GSTIN - see _usable_vendor_gstin.
    gstin = _usable_vendor_gstin(payload.get("vendor_gstin"))
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
    #
    # Ingestion sends source_sha256: the hash of the file as the vendor sent it.
    # For a PDF that equals what we would compute here. For a Word document
    # converted on the way in it does not, and only the original is stable -
    # LibreOffice stamps a creation time into every PDF it writes, so the same
    # .docx converted twice hashes differently and would never match itself.
    # Prefer what ingestion sent, and fall back for any caller that omits it.
    file_sha256 = payload.pop("source_sha256", None) or _file_sha256(base64_pdf)

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

    # Catch a day/month swap before anything is derived from the date - the due
    # date, the financial year in the dedupe key and the payment sheet all read
    # it, so a wrong date here propagates everywhere.
    date_correction = None
    corrected_date = _corrected_invoice_date(payload.get("invoice_date"))
    if corrected_date:
        date_correction = {
            "from": str(payload.get("invoice_date")),
            "to": corrected_date.isoformat(),
            "reason": "day/month order swapped by extraction",
            "at": datetime.utcnow(),
        }
        logger.warning(
            f"Invoice date {payload.get('invoice_date')!r} looks day/month swapped "
            f"against today; reading it as {corrected_date}"
        )
        payload["invoice_date"] = corrected_date.isoformat()

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

    # Worked out at ingestion as well as on accept, so a pending invoice already
    # shows when it will fall due rather than staying blank until someone
    # approves it. Recomputed on accept in case the date or vendor was corrected
    # during review.
    expected_payment_date = await _compute_expected_payment_date(payload)

    # Does this invoice ask to be paid into the account we have on file?
    bank_check = await _check_bank_details(payload, await _find_vendor_doc(payload))

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
        "invoice_number": payload.get("invoice_number"),
        "invoice_date": payload.get("invoice_date"),
        "total_amount": payload.get("total_amount"),
        "vendor_gstin": payload.get("vendor_gstin"),
        # resolve_vendor_zoho_contact has just written these into the payload.
        # Kept at the top level because they are the only reliable way to tie an
        # invoice back to its vendor: vendor_id exists for every vendor, while
        # 42 of 75 have no GSTIN and names differ between invoice and master.
        # Without this, a later lookup has nothing exact to match on.
        "vendor_id": payload.get("vendor_id"),
        "zoho_contact_id": payload.get("zoho_contact_id"),
        "expected_payment_date": expected_payment_date,
        # Present only when the date was corrected, so a reviewer can see it
        # happened rather than wondering why the date differs from the PDF.
        "invoice_date_corrected": date_correction,
        # None when the invoice carries no bank details, which is most of them.
        "bank_check": bank_check,
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

    # --- Money actually paid -------------------------------------------------
    #
    # Filtered on paid_at, NOT created_at: "what did we pay in September" means
    # the month the money went out, not the month the invoice happened to be
    # ingested. Those are often different months, and using the wrong one
    # quietly misstates every figure here.
    #
    # The amount is taken from the accepted payload first, since that is what
    # was actually approved and sent to Zoho, falling back to the extracted
    # values. Invoices accepted before the payload was retained have no amount
    # anywhere and count as zero - there is nothing left to read.
    paid_match: Dict[str, Any] = {"status": "paid", **NOT_DELETED}
    if "created_at" in query:
        paid_match["paid_at"] = query["created_at"]
    if "vendor_name" in query:
        paid_match["vendor_name"] = query["vendor_name"]

    amount_field = {
        "$convert": {
            "input": {
                "$ifNull": [
                    "$accepted_data.total_amount",
                    {"$ifNull": ["$total_amount", "$invoice_data.total_amount"]},
                ]
            },
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }

    paid_totals = await invoices_col.aggregate([
        {"$match": paid_match},
        {"$group": {"_id": None, "total": {"$sum": amount_field}, "count": {"$sum": 1}}}
    ]).to_list(None)

    paid_by_vendor = await invoices_col.aggregate([
        {"$match": paid_match},
        {"$group": {
            "_id": {"$ifNull": ["$vendor_name", "Unknown"]},
            "total": {"$sum": amount_field},
            "count": {"$sum": 1}
        }},
        {"$sort": {"total": -1}},
        {"$limit": 10}
    ]).to_list(None)

    paid_timeline = await invoices_col.aggregate([
        {"$match": paid_match},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$paid_at"}},
            "total": {"$sum": amount_field},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]).to_list(None)

    # How many paid invoices carry no recoverable amount, so the UI can say so
    # rather than presenting an understated total as if it were complete.
    paid_without_amount = await invoices_col.count_documents({
        **paid_match,
        "accepted_data.total_amount": {"$exists": False},
        "total_amount": {"$exists": False},
        "invoice_data.total_amount": {"$exists": False},
    })

    return {
        "status_distribution": {item["_id"]: item["count"] for item in status_counts},
        "total": total_processed,
        "vendors": [{"vendor": item["_id"] or "Unknown", "count": item["count"]} for item in vendor_counts],
        "timeline": [{"date": item["_id"], "count": item["count"]} for item in timeline_counts],
        "total_email_invoices": total_email_invoices,
        "total_zoho_pushed": total_zoho_pushed,
        "total_duplicates_blocked": total_duplicates_blocked,
        # Invoice value, inclusive of GST. This is what was billed and approved,
        # not the cash that left the bank - TDS is deducted at payment and is
        # not tracked in this service, so the two differ by the withheld amount.
        "total_paid": round(paid_totals[0]["total"], 2) if paid_totals else 0,
        "paid_count": paid_totals[0]["count"] if paid_totals else 0,
        "paid_without_amount": paid_without_amount,
        "paid_by_vendor": [
            {"vendor": v["_id"], "total": round(v["total"], 2), "count": v["count"]}
            for v in paid_by_vendor
        ],
        "paid_timeline": [
            {"month": t["_id"], "total": round(t["total"], 2), "count": t["count"]}
            for t in paid_timeline
        ],
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
        {},
        {"_id": 0, "vendor_name": 1, "gstin": 1, "pan": 1, "vendor_id": 1,
         "ledger_name": 1, "ledger_id": 1, "credit_days": 1, "bank_details": 1},
    ).sort("vendor_name", 1)
    vendors = await cursor.to_list(length=5000)
    return [
        {
            **v,
            # The mapping screen cares whether bank details exist, not what they
            # are - account numbers have no reason to reach a browser.
            "has_bank_details": bool(v.pop("bank_details", None)),
        }
        for v in vendors if v.get("vendor_name")
    ]


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
                    
            # The invoice payload used to be deleted at this point, which threw
            # away the invoice number, date and amount the moment a bill was
            # approved - leaving no way to build a payment sheet, audit what was
            # sent, or even tell two accepted invoices apart. It is kept now.
            #
            # accepted_data is the payload that actually reached Zoho, which can
            # differ from invoice_data if a human corrected the extraction. The
            # difference between the two is worth keeping: it shows exactly where
            # extraction is weak.
            expected_payment_date = await _compute_expected_payment_date(payload_data)

            await invoices_col.update_one(
                {"_id": id},
                {
                    "$set": {
                        "status": "accepted",
                        "updated_at": datetime.utcnow(),
                        "accepted_data": payload_data,
                        # Promoted to the top level so the payment sheet and any
                        # reporting can query them without reaching into a nested
                        # document.
                        "invoice_number": payload_data.get("invoice_number"),
                        "invoice_date": payload_data.get("invoice_date"),
                        "total_amount": payload_data.get("total_amount"),
                        "vendor_gstin": payload_data.get("vendor_gstin"),
                        "vendor_id": payload_data.get("vendor_id"),
                        "zoho_contact_id": payload_data.get("zoho_contact_id"),
                        "zoho_bill_id": bill_id,
                        "expected_payment_date": expected_payment_date,
                    }
                }
            )

            return {
                "status": "accepted",
                "zoho_bill": created_bill,
                "verification_status": verify_status,
                "expected_payment_date": expected_payment_date.date().isoformat()
                if expected_payment_date else None,
            }
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


# --- Payment sheet ----------------------------------------------------------
#
# Deliberately identical in shape to the sheet the reimbursement portal already
# produces (AdminDashboard.tsx, exportApprovedPaymentSheet): 25 unlabelled
# columns A-Y in the order the bank's upload expects. Finance feeds both sheets
# to the same place, so the layout is not ours to improve on.
PAYMENT_SHEET_COMPANY = "BIZBOOST"
PAYMENT_SHEET_PRODUCT = "VPAY"
PAYMENT_SHEET_DEBIT_ACCOUNT = 7411623583


def _payment_sheet_row(invoice: Dict[str, Any], vendor: Optional[Dict[str, Any]]) -> List[Any]:
    bank = (vendor or {}).get("bank_details") or {}
    bank_name = str(bank.get("bank") or "")

    data = invoice.get("accepted_data") or invoice.get("invoice_data") or {}
    amount = _norm_amount(
        data.get("total_amount") if data.get("total_amount") is not None
        else invoice.get("total_amount")
    )

    due = invoice.get("expected_payment_date")
    if isinstance(due, datetime):
        due_text = due.strftime("%d/%m/%Y")
    else:
        fallback = _next_payment_cycle(datetime.utcnow().date())
        due_text = fallback.strftime("%d/%m/%Y") if fallback else ""

    invoice_number = (
        invoice.get("invoice_number")
        or data.get("invoice_number")
        or str(invoice.get("_id", ""))[:8].upper()
    )

    row: List[Any] = [""] * 25
    row[0] = PAYMENT_SHEET_COMPANY
    row[1] = PAYMENT_SHEET_PRODUCT
    # Same bank as the company means an internal transfer rather than NEFT.
    row[2] = "IFT" if "kotak" in bank_name.lower() else "NEFT"
    row[4] = due_text
    row[6] = PAYMENT_SHEET_DEBIT_ACCOUNT
    row[7] = amount if amount is not None else ""
    row[8] = "M"
    row[10] = invoice.get("vendor_name") or data.get("vendor_name") or ""
    row[11] = bank_name
    row[12] = str(bank.get("ifsc") or "")
    row[13] = str(bank.get("account_number") or "")
    row[23] = invoice_number
    row[24] = invoice_number
    return row


# Legal forms that appear on an invoice but rarely in the vendor master, and
# never distinguish one vendor from another.
_LEGAL_FORMS = ("PRIVATELIMITED", "PVTLTD", "PRIVATELTD", "LIMITED", "LLP",
                "PVT", "LTD", "INC", "CORP", "COMPANY", "OPC")


def _norm_vendor_name(value: Any) -> str:
    """
    Reduce a vendor name to its distinguishing part.

    "C.R.Sanjay & Co." and "C R Sanjay and Co" are the same firm; so are
    "Treebo Hospitality Ventures Private Limited" and "TREEBO HOSPITALITY
    VENTURES PVT LTD". Ampersands become AND, punctuation goes, and trailing
    legal forms are stripped.
    """
    text = re.sub(r"[^A-Z0-9]", "", str(value or "").upper().replace("&", "AND"))
    changed = True
    while changed:
        changed = False
        for form in _LEGAL_FORMS:
            if text.endswith(form) and len(text) > len(form) + 4:
                text = text[: -len(form)]
                changed = True
    return text


async def _match_vendor_by_name(vendors_col, invoice_name: str) -> Optional[Dict[str, Any]]:
    """
    Last-resort vendor match for invoices with no vendor_id or GSTIN left.

    Matches the vendor's own name or any entry in its `aliases` list, after
    normalising punctuation, case and legal suffixes.

    Deliberately NOT fuzzy. Vendors are recorded under a working name while
    invoices carry the legal one - "Sumo Technologies" against "SUMO
    TECHNOLOGIES PVT LTD" - and it is tempting to close that gap by guessing.
    But the output of this lookup is a bank account number, and a near-miss
    sends money to the wrong vendor. So the gap is closed by recording the
    invoice's spelling as an alias, which is a decision someone made once and
    can check, rather than a similarity score.

    Populate aliases with: python suggest_vendor_aliases.py
    """
    target = _norm_vendor_name(invoice_name)
    if len(target) < 5:
        return None

    # Only vendors that actually have bank details are candidates: matching one
    # without them achieves nothing and widens the chance of a wrong hit.
    candidates = await vendors_col.find(
        {"bank_details": {"$exists": True}},
        # aliases MUST be in this projection - without it every vendor looks as
        # though it has none and the alias list silently does nothing.
        {"vendor_name": 1, "aliases": 1, "bank_details": 1, "vendor_id": 1},
    ).to_list(None)

    matches = []
    for v in candidates:
        names = [v.get("vendor_name")] + list(v.get("aliases") or [])
        if any(_norm_vendor_name(n) == target for n in names):
            matches.append(v)

    # Exactly one or nothing. Two candidates means the alias list is wrong and
    # needs fixing, not a coin toss over which bank account gets the money.
    return matches[0] if len(matches) == 1 else None


@router.post("/payment-sheet")
async def payment_sheet(payload: Dict[str, Any]):
    """
    Build the bank payment sheet for a set of accepted invoices.

    { "ids": [...] }

    Bank details are read here rather than sent to the browser, so account
    numbers never leave the server except inside the generated file. Vendors
    with no bank details on record still get a row - with the bank columns
    blank - and are named in the X-Missing-Bank-Details response header, so
    nothing is silently dropped from a payment run.
    """
    import io
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse

    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="'ids' must be a non-empty list")

    invoices = await invoices_col.find(
        {"_id": {"$in": ids}, "status": {"$in": ["accepted", "paid"]}, **NOT_DELETED}
    ).to_list(length=len(ids))

    if not invoices:
        raise HTTPException(
            status_code=400,
            detail="None of the selected invoices are accepted, so none can be paid"
        )

    vendors_col = db["vendors"]
    rows, missing_bank, no_amount, approximate = [], [], [], []
    for invoice in invoices:
        data = invoice.get("accepted_data") or invoice.get("invoice_data") or {}

        # Invoices accepted before the payload was retained had their amount
        # deleted along with everything else and it cannot be recovered here.
        # The row is kept with a blank amount so Finance can fill it in from the
        # PDF, and the count is reported so nobody uploads the sheet assuming
        # every line is complete.
        amount = _norm_amount(
            data.get("total_amount") if data.get("total_amount") is not None
            else invoice.get("total_amount")
        )
        if not amount:
            no_amount.append(
                invoice.get("invoice_number")
                or data.get("invoice_number")
                or invoice.get("vendor_name")
                or str(invoice.get("_id", ""))[:8]
            )

        # Same ladder resolve_vendor_zoho_contact uses, including the fall back
        # to name. Invoices accepted before the payload was retained have no
        # vendor_id and no GSTIN left - but they do still carry vendor_name, and
        # without using it every one of them looks like a vendor with no bank
        # details on record, which is not what is wrong with them.
        # Top level first - that is where it is kept from ingestion onward, and
        # it survives everything. The payload is the fallback for older records.
        vendor_id = invoice.get("vendor_id") or data.get("vendor_id")
        gstin = _usable_vendor_gstin(invoice.get("vendor_gstin") or data.get("vendor_gstin"))
        vendor_name = invoice.get("vendor_name") or data.get("vendor_name")

        vendor = None
        if vendor_id:
            vendor = await vendors_col.find_one({"vendor_id": vendor_id})
        if not vendor and gstin:
            vendor = await vendors_col.find_one({"gstin": str(gstin).strip()})
        if not vendor and vendor_name:
            vendor = await _match_vendor_by_name(vendors_col, vendor_name)
            if vendor:
                approximate.append(f"{vendor_name} -> {vendor.get('vendor_name')}")

        if not (vendor or {}).get("bank_details"):
            name = invoice.get("vendor_name") or "Unknown"
            if name not in missing_bank:
                missing_bank.append(name)

        rows.append(_payment_sheet_row(invoice, vendor))


    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Vendor and Re-imbursement"
    for row in rows:
        sheet.append(row)

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    filename = f"Payment_Sheet_{datetime.utcnow().date().isoformat()}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Missing-Bank-Details": ", ".join(missing_bank),
            "X-Skipped-No-Amount": str(len(no_amount)),
            "X-Approximate-Matches": " | ".join(approximate),
            "X-Row-Count": str(len(rows)),
            "Access-Control-Expose-Headers":
                "X-Missing-Bank-Details, X-Skipped-No-Amount, X-Approximate-Matches, X-Row-Count",
        },
    )


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

