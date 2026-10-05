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
    mongo_uri: str = "mongodb://localhost:27017"
    gdrive_link: str = ""
    tds_item_id: str = ""
    upload_dir: str = "/app/uploads"
    # Our own GSTIN, as the buyer on every invoice we receive. A vendor must
    # never be matched on it: extraction sometimes picks the buyer's GSTIN up as
    # the vendor's, and any vendor record carrying this value would then absorb
    # those invoices and lend them its credit terms and bank account.
    company_gstin: str = "29AAPFB4349A1ZG"
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
