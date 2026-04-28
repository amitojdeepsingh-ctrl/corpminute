import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Stripe
    stripe_secret_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")
    stripe_price_solo_monthly: str = Field(default="")
    stripe_price_active_monthly: str = Field(default="")
    stripe_price_catchup: str = Field(default="")
    stripe_price_special_resolution: str = Field(default="")

    # Resend
    resend_api_key: str = Field(default="")
    from_email: str = Field(default="hello@corpminute.ca")
    creator_email: str = Field(default="")

    # LinkedIn
    linkedin_access_token: str = Field(default="")
    linkedin_person_urn: str = Field(default="")

    # App
    secret_key: str = Field(default="change-me")
    domain: str = Field(default="corpminute.ca")
    port: int = Field(default=8080, alias="PORT")
    data_dir: Path = Field(default=Path("./data"))
    log_dir: Path = Field(default=Path("./logs"))

    # Creator
    creator_wallet: str = Field(default="")

    stripe_publishable_key: str = Field(default="")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
        populate_by_name = True

    def ensure_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
