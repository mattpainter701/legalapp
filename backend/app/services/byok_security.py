"""Fail-closed validation for tenant-controlled LLM provider endpoints."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException


GEMINI_OPENAI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"
_AZURE_SUFFIXES = (
    ".openai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
)
_DEPLOYMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _bad_endpoint(detail: str) -> HTTPException:
    return HTTPException(
        status_code=400, detail=f"Invalid customer LLM endpoint: {detail}"
    )


def normalize_customer_llm_endpoint(provider: str, endpoint: str | None) -> str:
    """Return an allowlisted provider URL or reject it before persistence/use."""
    provider = (provider or "").strip().lower()
    raw = (endpoint or "").strip()
    if provider == "gemini":
        if raw and raw.rstrip("/") != GEMINI_OPENAI_ENDPOINT.rstrip("/"):
            raise _bad_endpoint("Gemini uses the fixed Google API endpoint")
        return GEMINI_OPENAI_ENDPOINT
    if provider != "copilot":
        raise _bad_endpoint("unsupported provider")
    if not raw:
        raise _bad_endpoint("an Azure OpenAI endpoint is required")

    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise _bad_endpoint("HTTPS is required")
    if parts.username or parts.password:
        raise _bad_endpoint("userinfo is not allowed")
    if parts.query or parts.fragment:
        raise _bad_endpoint("query strings and fragments are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise _bad_endpoint("invalid port") from exc
    if port not in (None, 443):
        raise _bad_endpoint("only port 443 is allowed")
    hostname = (parts.hostname or "").rstrip(".").lower()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise _bad_endpoint("IP address literals are not allowed")
    if not hostname or not any(hostname.endswith(suffix) for suffix in _AZURE_SUFFIXES):
        raise _bad_endpoint("host must be an approved Azure AI domain")
    resource_label = hostname.split(".", 1)[0]
    if (
        not resource_label
        or resource_label.startswith("-")
        or resource_label.endswith("-")
    ):
        raise _bad_endpoint("invalid Azure resource hostname")
    lowered_path = parts.path.lower()
    if "\\" in parts.path or "%2f" in lowered_path or "%5c" in lowered_path:
        raise _bad_endpoint("encoded or backslash path separators are not allowed")
    path = parts.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urlunsplit(("https", hostname, path, "", ""))


def validate_customer_llm_deployment(
    provider: str, deployment: str | None
) -> str | None:
    value = (deployment or "").strip() or None
    if provider == "copilot" and not value:
        raise HTTPException(status_code=400, detail="Azure deployment is required")
    if value and not _DEPLOYMENT_RE.fullmatch(value):
        raise HTTPException(
            status_code=400, detail="Invalid customer LLM deployment name"
        )
    return value
