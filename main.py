from fastapi import FastAPI, HTTPException, status
from typing import List, Dict, Any
import logging

from config import settings
from token_service import token_service
from zoho_client import zoho_books_client
from schemas import BillCreateRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoho Books API Gateway",
    description="A server-to-server integration bridging local operations to Zoho Books",
    version="1.0.0"
)

@app.on_event("startup")
async def startup_event():
    # Attempt to load tokens on startup, but we don't block if they don't exist
    # If they are missing, requests will just fail with 500 until auth_setup is run.
    logger.info("Starting up FastAPI application for Zoho Books integration.")

@app.get("/vendors", response_model=List[Dict[str, Any]])
async def list_vendors(page: int = 1, per_page: int = 200):
    """Retrieve a list of vendors from Zoho Books."""
    try:
        vendors = await zoho_books_client.get_vendors(page=page, per_page=per_page)
        return vendors
    except Exception as e:
        logger.error(f"Error fetching vendors: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/items", response_model=List[Dict[str, Any]])
async def list_items(page: int = 1, per_page: int = 200):
    """Retrieve a list of items from Zoho Books."""
    try:
        items = await zoho_books_client.get_items(page=page, per_page=per_page)
        return items
    except Exception as e:
        logger.error(f"Error fetching items: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/bills", response_model=List[Dict[str, Any]])
async def list_bills(page: int = 1, per_page: int = 200):
    """Retrieve a list of bills from Zoho Books."""
    try:
        bills = await zoho_books_client.get_bills(page=page, per_page=per_page)
        return bills
    except Exception as e:
        logger.error(f"Error fetching bills: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.post("/bills", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_new_bill(bill: BillCreateRequest):
    """Create a new bill in Zoho Books."""
    try:
        created_bill = await zoho_books_client.create_bill(bill)
        return created_bill
    except Exception as e:
        logger.error(f"Error creating bill: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/health")
def health_check():
    """Health check endpoint to verify the service is running."""
    # We could also check token validity here, but local check is simpler.
    has_tokens = token_service.access_token is not None and token_service.refresh_token is not None
    return {
        "status": "healthy",
        "tokens_initialized": has_tokens
    }
