from pydantic import BaseModel, Field
from typing import List

class LineItem(BaseModel):
    item_id: str
    rate: float
    quantity: float

class BillCreateRequest(BaseModel):
    vendor_id: str
    date: str
    line_items: List[LineItem]
    bill_number: str | None = None
    reference_number: str | None = None
    total: float | None = None
