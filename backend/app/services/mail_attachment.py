"""Bounded, in-memory approved document bytes; never a path or provider URL."""

from dataclasses import dataclass

MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content: bytes
    content_type: str = DOCX_MIME

    def __post_init__(self):
        if not self.content or len(self.content) > MAX_ATTACHMENT_BYTES:
            raise ValueError("Document attachments must be between 1 byte and 2 MiB")
        if not self.filename or any(c in self.filename for c in "\r\n/\\"):
            raise ValueError("Attachment filename must be a safe basename")
