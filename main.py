from fastapi import FastAPI, HTTPException, status
from typing import List, Dict, Any
import logging

from config import settings
from token_service import token_service
from zoho_client import zoho_books_client
from schemas import IncomingBillPayload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoho Books API Gateway",
    description="A server-to-server integration bridging local operations to Zoho Books",
    version="1.0.0"
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

upload_dir = settings.upload_dir
os.makedirs(upload_dir, exist_ok=True)
logger.info(f"Using upload directory: {upload_dir}")
app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")

from verification_router import router as verification_router
app.include_router(verification_router)

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

@app.get("/chartofaccounts", response_model=List[Dict[str, Any]])
async def list_chartofaccounts(page: int = 1, per_page: int = 200):
    """Retrieve a list of chart of accounts from Zoho Books."""
    try:
        accounts = await zoho_books_client.get_chartofaccounts(page=page, per_page=per_page)
        return accounts
    except Exception as e:
        logger.error(f"Error fetching chart of accounts: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/tds-taxes", response_model=List[Dict[str, Any]])
async def list_tds_taxes():
    """Retrieve a list of active TDS taxes from Zoho Books."""
    try:
        tds_taxes = await zoho_books_client.get_tds_taxes()
        return tds_taxes
    except Exception as e:
        logger.error(f"Error fetching TDS taxes: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/taxes", response_model=List[Dict[str, Any]])
async def list_standard_taxes():
    """Retrieve a list of standard taxes from Zoho Books."""
    try:
        taxes = await zoho_books_client.get_standard_taxes()
        return taxes
    except Exception as e:
        logger.error(f"Error fetching standard taxes: {e}")
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

@app.post("/bills", status_code=status.HTTP_201_CREATED)
async def create_new_bill(payload: List[IncomingBillPayload] | IncomingBillPayload):
    """Create a new bill (or bills) in Zoho Books from webhook payload."""
    try:
        bills_to_process = payload if isinstance(payload, list) else [payload]
        results = []
        for bill in bills_to_process:
            created_bill = await zoho_books_client.create_bill(bill)
            results.append(created_bill)
        
        # If a single object was sent, return a single object response
        if not isinstance(payload, list):
            return results[0]
        return results
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
