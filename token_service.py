import json
import os
import time
from typing import Optional, Dict, Any
import httpx
import logging

from config import settings

logger = logging.getLogger(__name__)

TOKEN_FILE = "tokens.json"

class TokenService:
    def __init__(self, token_file: str = TOKEN_FILE):
        self.token_file = token_file
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0
        self._load_tokens()

    def _load_tokens(self) -> None:
        if os.path.exists(self.token_file):
            logger.info("Loading tokens from file.")
            with open(self.token_file, "r") as f:
                data = json.load(f)
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.expires_at = data.get("expires_at", 0)

    def _save_tokens(self) -> None:
        logger.info("Saving tokens to file.")
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }
        with open(self.token_file, "w") as f:
            json.dump(data, f)

    def is_token_expired(self) -> bool:
        # Buffer of 60 seconds
        return time.time() > (self.expires_at - 60)

    async def get_valid_access_token(self) -> str:
        if not self.access_token or not self.refresh_token:
            raise ValueError("Tokens are not initialized. Please run auth_setup.py first.")

        if self.is_token_expired():
            logger.info("Access token is expired or expiring soon. Refreshing...")
            await self.refresh_access_token()
        
        return self.access_token

    async def fetch_initial_tokens(self, auth_code: str) -> None:
        """Exchanges initial auth code for access and refresh tokens."""
        url = f"{settings.zoho_domain}/oauth/v2/token"
        data = {
            "code": auth_code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": str(settings.redirect_uri),
            "grant_type": "authorization_code"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data)
            
            if response.status_code != 200:
                raise Exception(f"Failed to fetch initial tokens: {response.text}")
                
            resp_data = response.json()
            if "error" in resp_data:
                raise Exception(f"OAuth Error: {resp_data['error']}")
                
            self.access_token = resp_data["access_token"]
            self.refresh_token = resp_data["refresh_token"]
            # expiresIn is usually 3600 seconds
            self.expires_at = time.time() + resp_data.get("expires_in", 3600)
            self._save_tokens()
            logger.info("Successfully fetched and saved initial tokens.")
            logger.info(f"Refresh Token: {self.refresh_token}") # Important to show it once
            
    async def refresh_access_token(self) -> None:
        """Uses refresh tuple to get a new access token."""
        url = f"{settings.zoho_domain}/oauth/v2/token"
        data = {
            "refresh_token": self.refresh_token,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": str(settings.redirect_uri),
            "grant_type": "refresh_token"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data)
            
            if response.status_code != 200:
                raise Exception(f"Failed to refresh token: {response.text}")
                
            resp_data = response.json()
            if "error" in resp_data:
                raise Exception(f"OAuth Refresh Error: {resp_data['error']}")
                
            self.access_token = resp_data["access_token"]
            # Server sometimes returns a new refresh token, sometimes not
            if "refresh_token" in resp_data:
                self.refresh_token = resp_data["refresh_token"]
                
            self.expires_at = time.time() + resp_data.get("expires_in", 3600)
            self._save_tokens()
            logger.info("Successfully refreshed access token.")

token_service = TokenService()
