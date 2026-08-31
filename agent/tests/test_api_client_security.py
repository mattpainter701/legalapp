import ssl
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from clarity_agent import api_client
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


def test_system_trust_context_uses_tls_client_protocol(monkeypatch):
    captured = {}

    class FakeConnectionContext:
        def __init__(self, protocol):
            captured["protocol"] = protocol

    monkeypatch.setattr(
        api_client, "_ConnectionIsolatedTrustContext", FakeConnectionContext
    )

    assert isinstance(api_client._system_trust_context(), FakeConnectionContext)
    assert captured["protocol"] == ssl.PROTOCOL_TLS_CLIENT


def test_system_trust_context_keeps_certificate_validation_enabled():
    context = api_client._system_trust_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_system_trust_context_isolates_concurrent_tls_connections():
    context = api_client._system_trust_context()

    def wrap_connection(_):
        return context.wrap_bio(
            ssl.MemoryBIO(),
            ssl.MemoryBIO(),
            server_hostname="files.example.com",
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        ssl_objects = list(pool.map(wrap_connection, range(32)))

    connection_contexts = [ssl_object.context for ssl_object in ssl_objects]
    assert (
        len({id(connection_context) for connection_context in connection_contexts})
        == 32
    )
    assert all(
        connection_context.check_hostname is True
        and connection_context.verify_mode == ssl.CERT_REQUIRED
        for connection_context in connection_contexts
    )
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_system_trust_context_applies_httpx_alpn_to_each_connection(monkeypatch):
    context = api_client._system_trust_context()
    context.set_alpn_protocols(["h2", "http/1.1"])
    created = []

    class FakeConnectionContext:
        def __init__(self, protocol):
            self.protocol = protocol
            self.alpn_protocols = ()
            created.append(self)

        def set_alpn_protocols(self, alpn_protocols):
            self.alpn_protocols = tuple(alpn_protocols)

        def wrap_bio(self, *args, **kwargs):
            return self

    monkeypatch.setattr(api_client.truststore, "SSLContext", FakeConnectionContext)

    wrapped = context.wrap_bio(object(), object(), server_hostname="files.example.com")

    assert wrapped is created[0]
    assert wrapped.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert wrapped.alpn_protocols == ("h2", "http/1.1")


@pytest.mark.asyncio
async def test_client_passes_system_trust_context_to_httpx(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def aclose(self):
            pass

    context = ssl.create_default_context()
    monkeypatch.setattr(api_client, "_system_trust_context", lambda: context)
    monkeypatch.setattr(api_client.httpx, "AsyncClient", FakeAsyncClient)

    client = SaaSClient(_config("https://files.example.com"))
    await client.close()

    assert captured["verify"] is context
