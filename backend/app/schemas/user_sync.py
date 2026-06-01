from pydantic import BaseModel


class UserSyncRequest(BaseModel):
    provider: str = "microsoft"


class UserSyncResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    total: int = 0
    error: str | None = None


class UserSyncResponse(BaseModel):
    microsoft: UserSyncResult | None = None
    google: UserSyncResult | None = None
