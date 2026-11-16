from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class WebIdentity:
    subject: str


class BusinessAccess(BaseModel):
    business_id: str
    business_name: str
    user_id: str
    display_name: str
    role: str


@dataclass(frozen=True)
class TenantContext:
    identity: WebIdentity
    access: BusinessAccess


class CurrentUserResponse(BaseModel):
    businesses: list[BusinessAccess]
