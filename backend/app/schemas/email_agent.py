from pydantic import BaseModel


class EmailScanRequest(BaseModel):
    provider: str = "microsoft"
    user_id: str | None = None
    days: int = 7
    max_emails: int = 20


class EmailClassification(BaseModel):
    category: str
    urgency: str
    summary: str | None = None
    action_needed: str | None = None
    deadline_mentioned: str | None = None
    requires_response: bool = False
    suggested_response: str | None = None


class ProcessedEmail(BaseModel):
    email_id: str
    subject: str | None = None
    sender: str | None = None
    received: str | None = None
    classification: EmailClassification
    draft_response: str | None = None


class EmailScanResponse(BaseModel):
    provider: str
    user_id: str | None = None
    emails_processed: int
    results: list[ProcessedEmail]


class CalendarEventResponse(BaseModel):
    id: str
    provider: str
    subject: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None


class CalendarSyncRequest(BaseModel):
    provider: str = "microsoft"
    user_id: str | None = None
    sync_deadlines: bool = False


class CalendarSyncResponse(BaseModel):
    provider: str
    events: list[CalendarEventResponse]
    deadlines_created: int = 0
