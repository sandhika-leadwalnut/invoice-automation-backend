# Application Context and Architecture

## Overview
This application is a server-to-server integration backend built with **FastAPI** that bridges internal incoming invoice data with an external accounting system (**Zoho Books**). Furthermore, it provides a Human-in-the-Loop review capability before pushing invoices to Zoho.

The typical workflow handles automated invoice extraction payloads (e.g., from n8n) and routes them through a web dashboard UI (React) for verification:
1. **Ingestion**: Invoices are ingested as JSON webhooks into the `/verification/invoice` endpoint and stored in MongoDB with a `pending` status.
2. **Review & Action**: A dashboard UI fetches pending invoices. A reviewer can "accept", "edit", or "reject" the invoice. 
3. **Zoho Push**: Once an invoice is accepted or edited, the backend converts it into Zoho Books Bill Payload. It fetches the required vendor from Zoho based on the `vendor_gstin`, creates the bill, verifies the bill sync, and optionally attaches a Google Drive link comment.
4. **Metrics Tracking**: System-level metrics (e.g., invoices emailed, accepted bounds, vendor distribution) are logged to MongoDB and exposed to the dashboard via aggregation pipelines.

---

## Technical Stack
- **Web Framework**: FastAPI (Async ecosystem for high performance).
- **Database**: MongoDB (via `motor` AsyncIOMotorClient) for fast, non-blocking CRUD operations.
- **External Integration**: Zoho Books API v3.
- **HTTP Client**: `httpx` (asynchronous non-blocking HTTP requests).
- **Authentication**: Custom OAuth2 Token Service handling refresh cycles.

---

## Non-Functional Requirements (NFRs)

### 1. Security
- **OAuth2 Token Management**: The application separates the initial manual connection (`auth_setup.py`) from ongoing operations. It securely stores an access token and refresh token (`tokens.json`), rotating the access token in the background as needed.
- **CORS Headers**: Currently, `CORSMiddleware` is configured to `allow_origins=["*"]`. This guarantees the React Frontend can freely call the backend but it relies on internal network boundaries for security rather than API keys. 
- **Database Security**: Connection is facilitated via `config.py` picking up `mongo_uri` from environment variables, avoiding hardcoding connection strings in source code.

### 2. Failure Resiliency & Reliability
- **Automated Retry Policy**: The `ZohoBooksClient` implements proactive token refreshing. If a request hits a `401 Unauthorized` block (meaning the background token expired), it traps the error, unilaterally triggers a refresh to Zoho, and retries the exact request one more time without user/caller disruption.
- **Non-Blocking I/O**: Both the MongoDB client (`motor`) and HTTP client (`httpx`) operate asynchronously, meaning the backend won't easily exhaust its worker threads when dealing with slow database or remote API responses.
- **Payload Verification**: When pushing a bill to Zoho Books, the system attempts to fetch the created bill to cross-verify that the received `bill_number` matches the original payload request. If a discrepancy arises, it flags it as a mismatch.

### 3. Observability & Monitoring
- **Logging Subsystem**: Python's native `logging` is heavily utilized across endpoints and integrations to provide an audit trail of:
  - Token refresh activities (`token_service.py`).
  - Request boundaries and HTTP errors triggered from the Zoho API (`zoho_client.py`).
  - Invoice verification statuses (successes and mismatches tracking).
- **Business Level Metrics**: Dedicated observability through MongoDB collections (`invoice_email_metrics`, `zoho_push_metrics`). This allows the system to aggregate and track the entire pipeline flow:
  - How many invoices were received by email vs ingested.
  - Which statuses run through the pipeline (grouped dynamically).
  - Total invoices successfully synchronized directly to Zoho.

### 4. Maintainability
- **Schematization**: Relying on Pydantic `schemas.py` guarantees strict type-checking on incoming bill payloads, decoupling API integration issues from data validation issues.
- **Modular Design**: The codebase uses FastAPIs `APIRouter` to securely modularize different segments of logic (e.g., metric calculations and ingestion workflows separated logically via `verification_router.py`).
