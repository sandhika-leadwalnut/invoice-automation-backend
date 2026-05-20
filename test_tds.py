import asyncio
from zoho_client import zoho_books_client

async def main():
    for tax_type in ["tds_tax", "tds", "withholding_tax"]:
        print(f"Trying tax_type={tax_type}")
        try:
            data = await zoho_books_client._request("GET", "/settings/taxes", params={"tax_type": tax_type})
            print([t.get("tax_name") for t in data.get("taxes", [])])
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
