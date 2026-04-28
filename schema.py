"""
Customer data schema — one JSON file per corporation in data/
"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Literal
from pydantic import BaseModel, Field

from config import settings


class Director(BaseModel):
    name: str
    address: str
    appointed: str  # ISO date string


class Officer(BaseModel):
    name: str
    role: str
    appointed: str


class Shareholder(BaseModel):
    name: str
    share_class: str = "Common"
    quantity: int


class Corporation(BaseModel):
    corp_name: str
    corp_number: str = ""
    province: Literal["federal", "ontario", "bc", "alberta", "quebec", "other"] = "ontario"
    incorporation_date: str = ""
    fiscal_year_end: str = ""  # YYYY-MM-DD
    business_type: str = "Corporation"
    directors: list[Director] = Field(default_factory=list)
    officers: list[Officer] = Field(default_factory=list)
    shareholders: list[Shareholder] = Field(default_factory=list)
    stripe_customer_id: str = ""
    stripe_subscription_id: str = ""
    plan: Literal["solo", "active", "cpa", "catchup"] = "solo"
    cpa_email: str = ""
    customer_email: str = ""
    status: Literal["active", "pending", "cancelled"] = "pending"
    last_generated: str = ""
    resolutions_approved: bool = False
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # Internal tracking
    law_changes_pending: list[str] = Field(default_factory=list)
    deadline_alert_60_sent: bool = False
    deadline_alert_30_sent: bool = False


def corp_path(customer_id: str) -> Path:
    return settings.data_dir / f"{customer_id}.json"


def save_corp(customer_id: str, corp: Corporation) -> None:
    corp_path(customer_id).write_text(corp.model_dump_json(indent=2), encoding="utf-8")


def load_corp(customer_id: str) -> Optional[Corporation]:
    path = corp_path(customer_id)
    if not path.exists():
        return None
    return Corporation.model_validate_json(path.read_text(encoding="utf-8"))


def list_all_corps() -> dict[str, Corporation]:
    result = {}
    for p in settings.data_dir.glob("*.json"):
        customer_id = p.stem
        try:
            result[customer_id] = Corporation.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return result


def list_active_corps() -> dict[str, Corporation]:
    return {cid: c for cid, c in list_all_corps().items() if c.status == "active"}
