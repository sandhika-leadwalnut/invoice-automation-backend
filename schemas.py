from pydantic import BaseModel, Field
from typing import List, Optional

class IncomingLineItem(BaseModel):
    item_number: int | None = None
    description: str
    hsn_sac: str | None = None
    quantity: float
    unit: str | None = None
    unit_price: float
    amount: float | None = None
    tax_id: str | None = None
    tax_exemption_code: str | None = None

class IncomingBillPayload(BaseModel):
    invoice_number: str
    invoice_date: str
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    currency: str | None = None
    subtotal: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    tax_total: float | None = None
    total_amount: float | None = None
    line_items: List[IncomingLineItem]
