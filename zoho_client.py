import httpx
import logging
from typing import Dict, Any, List, Optional
from config import settings
from token_service import token_service
from schemas import IncomingBillPayload

logger = logging.getLogger(__name__)

class ZohoBooksClient:
    def __init__(self):
        self.base_url = f"{settings.api_domain}/books/v3"

    async def _get_headers(self) -> Dict[str, str]:
        access_token = await token_service.get_valid_access_token()
        return {
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Content-Type": "application/json"
        }

    def _get_params(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = {"organization_id": settings.organization_id}
        if params:
            p.update(params)
        return p

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, json_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        # We attempt the request. If it fails with 401 Unauthorized, we force refresh the token and retry once.
        max_retries = 1
        
        for attempt in range(max_retries + 1):
            headers = await self._get_headers()
            req_params = self._get_params(params)
            
            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, headers=headers, params=req_params, json=json_data)
                
                if response.status_code == 401:
                    logger.warning(f"401 Unauthorized from Zoho Books (Attempt {attempt + 1}). Token might have been revoked or expired.")
                    if attempt < max_retries:
                        logger.info("Forcing token refresh and retrying...")
                        await token_service.refresh_access_token()
                        continue
                    else:
                        raise Exception("Failed to authenticate with Zoho Books even after retry.")
                
                # Capture actual JSON error message if possible before raising for status
                data = None
                try:
                    data = response.json()
                except Exception:
                    pass
                
                if response.status_code >= 400:
                    error_msg = data.get("message", response.text) if data else response.text
                    raise Exception(f"HTTP {response.status_code}: {error_msg}")
                    
                if data and data.get("code") != 0:
                    raise Exception(f"Zoho API Error: {data.get('message', 'Unknown Error')} (Code: {data.get('code')})")
                    
                return data or {}

    async def get_vendors(self, page: int = 1, per_page: int = 200) -> List[Dict[str, Any]]:
        """List all vendors."""
        params = {"page": page, "per_page": per_page}
        
        # We filter simply for vendors (Zoho Books defines Vendors as contact_type == 'vendor')
        params["contact_type"] = "vendor"
        data = await self._request("GET", "/contacts", params=params)
        return data.get("contacts", [])

    async def get_items(self, page: int = 1, per_page: int = 200) -> List[Dict[str, Any]]:
        """List all items."""
        params = {"page": page, "per_page": per_page}
        data = await self._request("GET", "/items", params=params)
        return data.get("items", [])

    async def get_bills(self, page: int = 1, per_page: int = 200) -> List[Dict[str, Any]]:
        """List all bills."""
        params = {"page": page, "per_page": per_page}
        data = await self._request("GET", "/bills", params=params)
        return data.get("bills", [])

    async def get_tds_taxes(self) -> List[Dict[str, Any]]:
        """List all active TDS taxes."""
        params = {
            "page": 1,
            "per_page": 100,
            "filter_by": "Taxes.All",
            "is_tds_request": "true",
            "usestate": "false"
        }
        data = await self._request("GET", "/settings/taxes", params=params)
        return data.get("taxes", [])

    async def get_standard_taxes(self) -> List[Dict[str, Any]]:
        """List all standard active taxes (excluding TDS)."""
        params = {
            "page": 1,
            "per_page": 100,
            "filter_by": "Taxes.Active"
        }
        data = await self._request("GET", "/settings/taxes", params=params)
        # Filter out TDS taxes to only get standard GST/IGST taxes
        taxes = data.get("taxes", [])
        return [t for t in taxes if t.get("tax_specific_type") != "tds"]

    async def get_vendor_by_gstin(self, gstin: str) -> Optional[Dict[str, Any]]:
        """Look up a vendor by their GSTIN."""
        if not gstin:
            return None
            
        params = {"gst_no": gstin, "contact_type": "vendor"}
        data = await self._request("GET", "/contacts", params=params)
        contacts = data.get("contacts", [])
        return contacts[0] if contacts else None

    async def get_bill(self, bill_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific bill by ID."""
        data = await self._request("GET", f"/bills/{bill_id}")
        return data.get("bill")

    async def create_bill(self, bill_request: IncomingBillPayload) -> Dict[str, Any]:
        """Create a new bill."""
        if not bill_request.vendor_gstin:
            raise Exception("Cannot create bill: Vendor GSTIN is missing.")
            
        vendor = await self.get_vendor_by_gstin(bill_request.vendor_gstin)
        if not vendor:
            raise Exception(f"No vendor found with GSTIN: {bill_request.vendor_gstin}")
            
        vendor_id = vendor.get("contact_id")

        line_items = []
        for item in bill_request.line_items:
            line_payload = {
                "item_id": item.item_id if item.item_id else settings.default_item_id,
                "name": item.description,
                "description": item.description,
                "rate": item.unit_price,
                "quantity": item.quantity,
            }
            if item.hsn_sac:
                sanitized_hsn = "".join(filter(str.isdigit, str(item.hsn_sac)))
                if sanitized_hsn:
                    line_payload["hsn_or_sac"] = sanitized_hsn[:8]
            if item.tax_id:
                line_payload["tax_id"] = item.tax_id
            elif item.tax_exemption_code:
                line_payload["tax_exemption_code"] = item.tax_exemption_code
            else:
                line_payload["tax_exemption_code"] = "NON-GST"
                
            if getattr(bill_request, 'tds_tax_id', None):
                line_payload["tds_tax_id"] = bill_request.tds_tax_id
                
            line_items.append(line_payload)
            
        payload = {
            "vendor_id": vendor_id,
            "date": bill_request.invoice_date,
            "bill_number": bill_request.invoice_number,
            "line_items": line_items,
        }

        data = await self._request("POST", "/bills", json_data=payload)
        return data.get("bill", {})

    async def add_bill_comment(self, bill_id: str, description: str) -> Dict[str, Any]:
        """Add a comment to a bill."""
        payload = {"description": description}
        data = await self._request("POST", f"/bills/{bill_id}/comments", json_data=payload)
        return data.get("comment", {})

zoho_books_client = ZohoBooksClient()
