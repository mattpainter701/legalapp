"""E-signature provider interface + factory.

Only one provider exists: the ``internal`` provider, which collects signatures
inside the client portal and needs no external dispatch. The interface stays
so the router does not have to know that, and so a future provider would slot
in behind the same ``send`` contract.
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

    def validate_request(self, request) -> None:
        """Provider-specific preflight hook for persisted request contracts."""
        return None


def get_provider(name: str) -> ESignProvider:
    """Resolve a provider by name. Only the internal portal provider exists."""
    if name in (None, "", "internal"):
        from app.services.esign.internal import InternalProvider

        return InternalProvider()
    raise ValueError(f"Unknown e-signature provider: {name}")
