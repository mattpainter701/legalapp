"""Dropbox Sign provider adapter.

The adapter is intentionally fail-closed when credentials are absent. Provider
success is only the envelope id; completion is accepted later through the
authenticated webhook reconciler.
"""

import hashlib
import json
import httpx

from app.config import get_settings

from app.services.esign.base import ESignProvider
from app.services.esign.placement import (
    PlacementError,
    validate_pdf_geometry,
    validate_placements,
    to_dropbox_form_field,
)


class DropboxSignProvider(ESignProvider):
    name = "dropbox_sign"

    async def send(self, request) -> str | None:
        settings = get_settings()
        if not settings.DROPBOX_SIGN_API_KEY:
            raise RuntimeError("Dropbox Sign is not configured")
        source = getattr(request, "source_document_bytes", None)
        if not source:
            raise RuntimeError("Signing source bytes were not loaded")
        signers = list(request.signers)
        expected_digest = hashlib.sha256(source).hexdigest()
        data = {
            "title": request.source_document_filename or "LawHand document",
            "subject": "Please review and sign",
            "message": "Please review this document in Dropbox Sign.",
            "test_mode": "1" if settings.DEV_MODE else "0",
            "metadata[lawhand_request_id]": str(request.id),
            "metadata[tenant_id]": str(request.tenant_id),
        }
        for index, signer in enumerate(signers):
            data[f"signers[{index}][email_address]"] = signer.email
            data[f"signers[{index}][name]"] = signer.name
            data[f"signers[{index}][order]"] = str(signer.sign_order)
        raw_fields = getattr(request, "positioned_fields", None) or []
        validated = []
        if raw_fields:
            roles = [
                ((signer.role or "signer").strip() or "signer") for signer in signers
            ]
            if len(roles) != len(set(roles)):
                raise RuntimeError("Signer roles must be unique for positioned fields")
            try:
                validated = validate_placements(
                    raw_fields, source_sha256=expected_digest, signer_roles=set(roles)
                )
                validate_pdf_geometry(source, validated)
            except PlacementError as exc:
                raise RuntimeError(str(exc)) from exc
        role_to_index = {
            ((signer.role or "signer").strip() or "signer"): index
            for index, signer in enumerate(signers)
        }
        if validated:
            data["form_fields_per_document"] = json.dumps(
                [
                    to_dropbox_form_field(field, signer_index=role_to_index[field.role])
                    for field in validated
                ]
            )
        files = {
            "files[]": (
                request.source_document_filename or "document.pdf",
                source,
                "application/pdf",
            )
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.ESIGN_PROVIDER_BASE_URL.rstrip('/')}/signature_request/send",
                data=data,
                files=files,
                auth=(settings.DROPBOX_SIGN_API_KEY, ""),
            )
            response.raise_for_status()
            payload = response.json()
        envelope = payload.get("signature_request", {}).get("signature_request_id")
        if not envelope:
            raise RuntimeError("Dropbox Sign response did not contain an envelope id")
        return envelope
