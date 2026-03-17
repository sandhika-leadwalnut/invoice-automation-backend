# Zoho Books Integration Backend

A production-ready FastAPI backend for connecting to the Zoho Books API. This service handles server-to-server operations like fetching vendors, creating bills, etc. 

## Features
- **Automatic Token Management**: Initialized manually once, then refreshes tokens behind the scenes.
- **FastAPI Framework**: Automatic interactive OpenAPI docs.
- **Async httpx**: Fast non-blocking HTTP requests.

## Setup Instructions

### 1. Configure the Environment
Create a `.env` file in the root directory:
```env
CLIENT_ID=your_client_id
CLIENT_SECRET=your_client_secret
REDIRECT_URI=http://localhost:8000/callback
ZOHO_DOMAIN=https://accounts.zoho.com
API_DOMAIN=https://www.zohoapis.in
ORGANIZATION_ID=your_organization_id
```

### 2. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. One-Time Authentication Flow
Run the initial auth script to get the first set of tokens:
```bash
python auth_setup.py
```
This script will output an Authorization URL. Visit it in your browser, grant permissions, and copy the `code` parameter from the redirect URL back to the script.

**How it works**: The script exchanges the code for an `access_token` and `refresh_token`, and permanently saves them to `tokens.json`. You only need to do this ONCE. Subsequent API requests by the FastAPI service will use the tokens from `tokens.json`, and will seamlessly use the `refresh_token` to get a new `access_token` when needed.

### 4. Running the Server
```bash
uvicorn main:app --reload
```
View the interactive docs at: `http://localhost:8000/docs`.

### Using Cron Jobs
If you need to automatically sync bills on a schedule (e.g. daily), you can construct a simple python script leveraging `{zoho_books_client}` that fetches local records and creates bills, bypassing the FastAPI router completely. Simply ensure `tokens.json` and `.env` exist. Alternatively, invoke the FastAPI endpoints directly from a script:
```bash
# In your crontab format (runs daily at midnight)
0 0 * * * curl -X POST http://localhost:8000/bills -d '{"vendor_id": "123","date": "2024-03-01","line_items":[{"item_id":"456","rate":10,"quantity":1}]}' -H "Content-Type: application/json"
```

### Example Usage

**List Vendors (Contacts where contact_type=Vendor)**
```bash
curl -X GET http://localhost:8000/vendors
```

**Create a Bill**
```bash
curl -X POST http://localhost:8000/bills \
     -H "Content-Type: application/json" \
     -d '{
           "vendor_id": "123456",
           "date": "2024-03-01",
           "line_items": [
             {
               "item_id": "98765",
               "rate": 100,
               "quantity": 2
             }
           ]
         }'
```
