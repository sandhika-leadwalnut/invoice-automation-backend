import asyncio
from zoho_client import zoho_books_client
from token_service import token_service
async def get_test():
    await token_service.get_valid_access_token()
    print("Items:", await zoho_books_client.get_items(per_page=1))
    print("Vendor:", await zoho_books_client.get_vendor_by_gstin('29AAGCN0349C1ZN'))
asyncio.run(get_test())
