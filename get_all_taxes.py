import asyncio
import json
from zoho_client import zoho_books_client

async def main():
    try:
        data = await zoho_books_client._request("GET", "/settings/taxes")
        with open("taxes_dump.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Taxes dumped to taxes_dump.json")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
