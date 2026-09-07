"""Internal e-signature provider — typed signature captured in the client portal."""

from app.services.esign.base import ESignProvider


class InternalProvider(ESignProvider):
    name = "internal"

    async def send(self, request) -> str | None:
        if getattr(request, "positioned_fields", None):
            raise RuntimeError(
                "The internal portal does not support positioned signing fields; "
                "use a provider with positioned-tab support"
            )
        # Nothing to dispatch externally — signers sign in the client portal.
        # The router flips status to "sent"; completion is handled when the
        # last signer signs (see service.complete_request_if_done).
        return None
