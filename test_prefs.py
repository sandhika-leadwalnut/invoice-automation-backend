import asyncio
from zoho_client import zoho_books_client
import json

async def main():
    try:
        data = await zoho_books_client._request("GET", "/settings/preferences")
        print("Success prefs")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
