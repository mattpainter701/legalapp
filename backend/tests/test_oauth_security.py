"""Tests for app.utils.oauth_security: PKCE, config validation, id_token verification."""

import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwt as jose_jwt

from app.utils import oauth_security as oauth_sec


def _b64url_uint(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    numbers = key.public_key().public_numbers()
    jwk_dict = {
        "kty": "RSA",
        "kid": "test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }
    return priv_pem, {"keys": [jwk_dict]}


def _sign(priv_pem: str, claims: dict) -> str:
    return jose_jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": "test-kid"})


def _at_hash(access_token: str) -> str:
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode()


def _patch_jwks(monkeypatch, jwks: dict):
    async def fake_get(self, url, *args, **kwargs):
        return httpx.Response(200, json=jwks, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


class TestPKCE:
    def test_generate_pkce_pair_matches_s256(self):
        verifier, challenge = oauth_sec.generate_pkce_pair()
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert challenge == expected
        assert 43 <= len(verifier) <= 128

    def test_generate_pkce_pair_is_random(self):
        v1, c1 = oauth_sec.generate_pkce_pair()
        v2, c2 = oauth_sec.generate_pkce_pair()
        assert v1 != v2
        assert c1 != c2

    def test_generate_nonce_is_random(self):
        assert oauth_sec.generate_nonce() != oauth_sec.generate_nonce()


class TestOAuthClientConfigured:
    @pytest.mark.parametrize(
        "client_id,client_secret,expected",
        [
            ("", "secret", False),
            ("client", "", False),
            ("   ", "secret", False),
            ("#placeholder", "secret", False),
            ("TODO-client-id", "secret", False),
            ("real-client-id", "real-secret", True),
        ],
    )
    def test_is_oauth_client_configured(self, client_id, client_secret, expected):
        assert oauth_sec.is_oauth_client_configured(client_id, client_secret) is expected


class TestVerifyGoogleIdToken:
    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        token = _sign(
            priv_pem,
            {
                "sub": "123",
                "aud": "my-client-id",
                "iss": "https://accounts.google.com",
                "email": "user@example.com",
                "email_verified": True,
                "nonce": "expected-nonce",
            },
        )
        claims = await oauth_sec.verify_google_id_token(
            token, client_id="my-client-id", expected_nonce="expected-nonce"
        )
        assert claims["email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_nonce_mismatch_rejected(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        token = _sign(
            priv_pem,
            {
                "sub": "123",
                "aud": "my-client-id",
                "iss": "https://accounts.google.com",
                "email": "user@example.com",
                "email_verified": True,
                "nonce": "actual-nonce",
            },
        )
        with pytest.raises(HTTPException) as exc:
            await oauth_sec.verify_google_id_token(
                token, client_id="my-client-id", expected_nonce="different-nonce"
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_at_hash_token_accepts_matching_access_token(
        self, rsa_keypair, monkeypatch
    ):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        access_token = "google-access-token"
        token = _sign(
            priv_pem,
            {
                "sub": "123",
                "aud": "my-client-id",
                "iss": "https://accounts.google.com",
                "email": "user@example.com",
                "email_verified": True,
                "at_hash": _at_hash(access_token),
            },
        )
        claims = await oauth_sec.verify_google_id_token(
            token,
            client_id="my-client-id",
            access_token=access_token,
        )
        assert claims["email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        token = _sign(
            priv_pem,
            {
                "sub": "123",
                "aud": "someone-elses-client-id",
                "iss": "https://accounts.google.com",
                "email": "user@example.com",
                "email_verified": True,
            },
        )
        with pytest.raises(HTTPException) as exc:
            await oauth_sec.verify_google_id_token(token, client_id="my-client-id")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_tampered_signature_rejected(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        token = _sign(
            priv_pem,
            {
                "sub": "123",
                "aud": "my-client-id",
                "iss": "https://accounts.google.com",
                "email": "attacker@example.com",
                "email_verified": True,
            },
        )
        header, payload, signature = token.split(".")
        # Swap in a forged payload claiming a different (unverified) email,
        # keeping the original signature — this must fail verification.
        forged_claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        forged_claims["email"] = "victim@example.com"
        forged_payload = (
            base64.urlsafe_b64encode(json.dumps(forged_claims).encode())
            .rstrip(b"=")
            .decode()
        )
        forged_token = f"{header}.{forged_payload}.{signature}"
        with pytest.raises(HTTPException):
            await oauth_sec.verify_google_id_token(forged_token, client_id="my-client-id")


class TestVerifyMicrosoftIdToken:
    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        tenant_guid = "11111111-1111-1111-1111-111111111111"
        token = _sign(
            priv_pem,
            {
                "sub": "abc",
                "aud": "ms-client-id",
                "iss": f"https://login.microsoftonline.com/{tenant_guid}/v2.0",
                "tid": tenant_guid,
                "email": "user@contoso.com",
                "nonce": "expected-nonce",
            },
        )
        claims = await oauth_sec.verify_microsoft_id_token(
            token,
            client_id="ms-client-id",
            tenant="common",
            expected_nonce="expected-nonce",
        )
        assert claims["email"] == "user@contoso.com"

    @pytest.mark.asyncio
    async def test_issuer_tenant_mismatch_rejected(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        token = _sign(
            priv_pem,
            {
                "sub": "abc",
                "aud": "ms-client-id",
                "iss": "https://login.microsoftonline.com/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/v2.0",
                "tid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "email": "user@contoso.com",
            },
        )
        with pytest.raises(HTTPException) as exc:
            await oauth_sec.verify_microsoft_id_token(
                token, client_id="ms-client-id", tenant="common"
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_non_microsoft_issuer_rejected(self, rsa_keypair, monkeypatch):
        priv_pem, jwks = rsa_keypair
        _patch_jwks(monkeypatch, jwks)
        token = _sign(
            priv_pem,
            {
                "sub": "abc",
                "aud": "ms-client-id",
                "iss": "https://evil.example.com/v2.0",
                "tid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "email": "user@contoso.com",
            },
        )
        with pytest.raises(HTTPException):
            await oauth_sec.verify_microsoft_id_token(
                token, client_id="ms-client-id", tenant="common"
            )
