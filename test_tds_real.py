import asyncio
from zoho_client import zoho_books_client
import json

async def main():
    try:
        params = {
            "page": 1,
            "per_page": 100,
            "filter_by": "Taxes.All",
            "is_tds_request": "true",
            "usestate": "false"
        }
        data = await zoho_books_client._request("GET", "/settings/taxes", params=params)
        with open("tds_taxes_dump.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Success, dumped to tds_taxes_dump.json")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
