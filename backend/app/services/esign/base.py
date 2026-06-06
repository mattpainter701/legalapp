"""E-signature provider interface + factory.

Providers abstract *dispatch* of a signature request. The ``internal`` provider
collects typed signatures inside the client portal (no external dispatch). Real
providers (Dropbox Sign / DocuSign) implement the same interface and would push
an envelope to the third party and reconcile via webhook.
"""

from abc import ABC, abstractmethod


class ESignProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def send(self, request) -> str | None:
        """Dispatch the request for signature.

        Returns an optional provider envelope id. For the ``internal`` provider
        this is a no-op (signing happens in the client portal).
        """
        raise NotImplementedError


def get_provider(name: str) -> ESignProvider:
    """Resolve a provider by name. Defaults to the internal provider."""
    if name in (None, "", "internal"):
        from app.services.esign.internal import InternalProvider

        return InternalProvider()
    if name in ("dropbox_sign", "docusign"):
        from app.services.esign.dropbox_sign import DropboxSignProvider

        return DropboxSignProvider()
    raise ValueError(f"Unknown e-signature provider: {name}")
