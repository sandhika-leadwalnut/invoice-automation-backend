from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import HttpUrl

class Settings(BaseSettings):
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    zoho_domain: str = "https://accounts.zoho.com"
    api_domain: str = "https://www.zohoapis.in"
    organization_id: str = ""
    default_item_id: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
