import httpx
import logging
from typing import Dict, Any, List, Optional
from config import settings
from token_service import token_service
from schemas import BillCreateRequest

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

    async def create_bill(self, bill_request: BillCreateRequest) -> Dict[str, Any]:
        """Create a new bill."""
        line_items = []
        for item in bill_request.line_items:
            line_items.append({
                "item_id": item.item_id,
                "rate": item.rate,
                "quantity": item.quantity
            })
            
        payload = {
            "vendor_id": bill_request.vendor_id,
            "date": bill_request.date,
            "line_items": line_items,
        }
        
        if bill_request.reference_number:
            payload["reference_number"] = bill_request.reference_number
            
        if getattr(bill_request, 'bill_number', None):
            payload["bill_number"] = bill_request.bill_number
            
        # Zoho Books automatically calculates the total based on line items, 
        # but if total is passed, it can be added. Usually let Zoho calculate it.

        data = await self._request("POST", "/bills", json_data=payload)
        return data.get("bill", {})

zoho_books_client = ZohoBooksClient()
