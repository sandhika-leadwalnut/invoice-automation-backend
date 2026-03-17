import asyncio
import urllib.parse
from config import settings
from token_service import token_service

def get_authorization_url() -> str:
    """Generates the authorization URL for the user to visit."""
    params = {
        "scope": "ZohoBooks.fullaccess.all",
        "client_id": settings.client_id,
        "response_type": "code",
        "access_type": "offline",
        "redirect_uri": str(settings.redirect_uri),
        "prompt": "consent"
    }
    query_string = urllib.parse.urlencode(params)
    return f"{settings.zoho_domain}/oauth/v2/auth?{query_string}"

async def main():
    print("="*60)
    print("ZOHO BOOKS OAUTH2 SETUP")
    print("="*60)
    
    if not settings.client_id or not settings.client_secret or not settings.redirect_uri:
        print("ERROR: Missing configuration.")
        print("Please ensure CLIENT_ID, CLIENT_SECRET, and REDIRECT_URI are set in your environment or .env file.")
        return

    auth_url = get_authorization_url()
    print("\n1. Please visit the following URL to authorize the application:")
    print(auth_url)
    print("\n2. After granting access, you will be redirected to your REDIRECT_URI.")
    print("   Look at the URL in your browser and copy the 'code' parameter.")
    
    auth_code = input("\nEnter the authorization code here: ").strip()
    
    if not auth_code:
        print("Authorization code cannot be empty. Exiting.")
        return

    print("\nFetching initial tokens...")
    try:
        await token_service.fetch_initial_tokens(auth_code)
        print("\nSUCCESS! Tokens have been fetched and saved to 'tokens.json'.")
        print("The service is now ready to run autonomously.")
        print(f"Make sure to set ORGANIZATION_ID in your configuration if you haven't yet.")
    except Exception as e:
        print(f"\nERROR: Failed to fetch tokens - {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
