import asyncio
from zoho_client import zoho_books_client
from pprint import pprint

async def main():
    data = await zoho_books_client._request("GET", "/settings/taxes")
    taxes = data.get("taxes", [])
    print("Available Taxes in Zoho Books:")
    for t in taxes:
        print(f"- Name: {t.get('tax_name')}, Type: {t.get('tax_type')}, ID: {t.get('tax_id')}")

if __name__ == "__main__":
    asyncio.run(main())
