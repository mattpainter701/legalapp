from types import SimpleNamespace

import pytest

from clarity_agent.api_client import SaaSClient


def _config(url):
    return SimpleNamespace(saas_url=url, api_key="key", agent_id="agent")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://files.example.com",
        "ftp://localhost",
        "files.example.com",
    ],
)
async def test_agent_rejects_non_tls_non_loopback_urls(url):
    with pytest.raises(ValueError, match="must use HTTPS"):
        SaaSClient(_config(url))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https://user:pass@files.example.com",
        "https://files.example.com?redirect=http://evil.example",
        "https://files.example.com/#fragment",
        "https://files.example.com:bad-port",
        "https://files example.com",
        "https://files.example.com\\@evil.example",
        "https://files.example.com/\x01",
    ],
)
async def test_agent_rejects_malformed_or_ambiguous_tls_urls(url):
    with pytest.raises(ValueError):
        SaaSClient(_config(url))


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://files.example.com", "http://127.0.0.1:8000"])
async def test_agent_allows_https_and_loopback_http(url):
    client = SaaSClient(_config(url))
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://localhost:8000", "http://[::1]:8000"])
async def test_agent_allows_loopback_http_names(url):
    client = SaaSClient(_config(url))
    await client.close()
