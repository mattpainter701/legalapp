"""Per-share SMB authentication.

The SaaS hands each share the credential it should mount with (see
``GET /api/v1/smb/agents/{id}/shares``). Credentials never touch this host's
disk: they live in memory for the lifetime of the process and are re-fetched on
the next poll, so revoking a credential in the admin console takes effect
without an agent reinstall. When a share has no credential attached, the agent
falls back to the identity in its local config — or, with neither, to the
account the service runs as (the Windows machine/service account).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("clarity_agent.auth")

NTLM = "ntlm"
KERBEROS = "kerberos"
GUEST = "guest"
MACHINE = "machine"


@dataclass(frozen=True)
class ShareCredential:
    """Credential material for one share, as delivered by the SaaS."""

    auth_method: str = MACHINE
    domain: str | None = None
    username: str | None = None
    password: str | None = None
    name: str | None = None
    credential_id: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - defensive, never log secrets
        return (
            f"ShareCredential(name={self.name!r}, method={self.auth_method!r}, "
            f"domain={self.domain!r}, username={self.username!r}, password=***)"
        )

    @property
    def describe(self) -> str:
        """Human-readable identity for logs, without the secret."""
        if self.auth_method == MACHINE:
            return "service account"
        if self.auth_method == GUEST:
            return "guest"
        who = f"{self.domain}\\{self.username}" if self.domain else (self.username or "?")
        return f"{who} ({self.auth_method})"

    @classmethod
    def from_share(cls, share: dict, config=None) -> ShareCredential:
        """Build the credential for a share, falling back to local config."""
        payload = share.get("credential") or {}
        if payload and (payload.get("username") or payload.get("auth_method")):
            return cls(
                auth_method=(payload.get("auth_method") or NTLM).lower(),
                domain=payload.get("domain") or None,
                username=payload.get("username") or None,
                password=payload.get("password") or None,
                name=payload.get("name"),
                credential_id=payload.get("credential_id"),
            )
        if config is not None and getattr(config, "smb_username", ""):
            return cls(
                auth_method=NTLM,
                domain=getattr(config, "smb_domain", "") or None,
                username=config.smb_username,
                password=getattr(config, "smb_password", "") or None,
                name="local config",
            )
        return cls(auth_method=MACHINE, name="service account")


def session_kwargs(credential: ShareCredential) -> dict:
    """Translate a credential into ``smbclient.register_session`` arguments."""
    if credential.auth_method == KERBEROS:
        # The host's ticket cache / keytab supplies the identity.
        return {"auth_protocol": "kerberos"}
    if credential.auth_method == GUEST:
        return {"username": "guest", "password": ""}
    if credential.auth_method == MACHINE:
        # No explicit identity: smbprotocol negotiates as the running account.
        return {}

    kwargs: dict = {
        "username": credential.username or "",
        "password": credential.password or "",
        "auth_protocol": "ntlm",
    }
    if credential.domain:
        # smbprotocol takes the domain through the username for NTLM.
        kwargs["username"] = f"{credential.domain}\\{credential.username or ''}"
    return kwargs


def connect(server: str, credential: ShareCredential, smbclient_module=None):
    """Register an SMB session for ``server`` using ``credential``.

    Returns the session on success and raises the underlying error on failure,
    so callers can surface the real reason (bad password, no route, access
    denied) to the admin console instead of a generic failure.
    """
    if smbclient_module is None:  # pragma: no cover - import kept lazy for tests
        import smbclient as smbclient_module

    kwargs = session_kwargs(credential)
    logger.info("Connecting to %s as %s", server, credential.describe)
    return smbclient_module.register_session(server, **kwargs)
