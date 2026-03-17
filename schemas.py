from pydantic import BaseModel, Field
from typing import List

class LineItem(BaseModel):
    item_id: str
    rate: float
    quantity: float
    tax_id: str | None = None
    tax_exemption_code: str | None = None

class BillCreateRequest(BaseModel):
    vendor_id: str | None = None
    gstin: str | None = None
    date: str
    line_items: List[LineItem]
    bill_number: str | None = None
    reference_number: str | None = None
    total: float | None = None
