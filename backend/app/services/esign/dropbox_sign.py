"""Stub for a certified external provider (Dropbox Sign / DocuSign).

Implements the same ``ESignProvider`` interface so the firm can switch a tenant
to a certified provider without changing routers. Wiring (envelope creation +
webhook reconciliation) is a follow-up.
"""

from app.services.esign.base import ESignProvider


class DropboxSignProvider(ESignProvider):
    name = "dropbox_sign"

    async def send(self, request) -> str | None:
        raise NotImplementedError(
            "External e-signature provider is not yet wired. "
            "Use the 'internal' provider, or add API credentials + webhook handling."
        )
