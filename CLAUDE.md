# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A two-part system for automating invoice ingestion into Zoho Books:
1. **FastAPI backend** — receives invoice JSON payloads (e.g., from n8n), stores them in MongoDB, handles human review, and pushes approved bills to Zoho Books via OAuth2.
2. **React review UI** (`review-ui/`) — a Vite+React dashboard for humans to accept, edit, or reject pending invoices before they reach Zoho.

## Development Commands

### Backend (Python/FastAPI)

```bash
# Activate virtualenv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# One-time OAuth2 setup (needed before the server can call Zoho)
python auth_setup.py

# Run the server (hot reload)
uvicorn main:app --reload

# Interactive API docs available at http://localhost:8000/docs
```

### Frontend (`review-ui/`)

```bash
cd review-ui

npm install
npm run dev       # dev server
npm run build     # production build
npm run lint      # eslint
npm run preview   # preview production build
```

## Architecture

### Request flow for invoice ingestion

1. External caller (n8n, cron, etc.) sends invoice JSON to `POST /verification/invoice`
2. `verification_router.py` stores it in MongoDB (`invoice_db.invoices`) with `status: "pending"`
3. The review UI fetches pending invoices from `GET /verification/invoices/pending`
4. User acts via `POST /verification/invoice/{id}/action` with `{"action": "accept"|"edit"|"reject", "data": {...}}`
5. On accept/edit, the backend calls `zoho_books_client.create_bill()` which looks up the vendor by GSTIN, then posts to Zoho Books

### Token management

`token_service.py` persists OAuth2 tokens in `tokens.json`. On every API call, `get_valid_access_token()` checks expiry (with a 60-second buffer) and silently refreshes when needed. The `ZohoBooksClient._request()` method also handles 401s with a single forced-refresh retry.

### Key modules

| File | Responsibility |
|---|---|
| `config.py` | Pydantic-settings config from `.env` |
| `token_service.py` | OAuth2 token lifecycle (load, save, refresh) |
| `zoho_client.py` | Zoho Books API v3 HTTP client |
| `schemas.py` | Pydantic models for incoming bill payloads |
| `main.py` | FastAPI app, CORS, direct `/bills`, `/vendors`, `/items` routes |
| `verification_router.py` | MongoDB-backed human-review workflow (`/verification/*` routes) |
| `auth_setup.py` | Interactive script for the one-time OAuth2 code exchange |

### Environment variables (`.env`)

```
CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
ZOHO_DOMAIN=https://accounts.zoho.com
API_DOMAIN=https://www.zohoapis.in
ORGANIZATION_ID
DEFAULT_ITEM_ID      # fallback item_id used for all Zoho line items
MONGO_URI=mongodb://localhost:27017
```

### Bill creation logic

`zoho_client.create_bill()` requires `vendor_gstin` in the payload — it resolves the Zoho `vendor_id` by querying contacts with `gst_no`. Line items all use `DEFAULT_ITEM_ID` as the Zoho item; tax is either set via `tax_id`, `tax_exemption_code`, or falls back to `"NON-GST"`.

### Invoice payload schema

The `IncomingBillPayload` schema (in `schemas.py`) is what the `/bills` POST endpoint and the verification accept/edit actions both deserialize into. The `n8n_payload.json` file contains a sample payload matching this schema.
