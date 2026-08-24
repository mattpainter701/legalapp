"""Secure storage for SMB file share credentials, scoped per tenant.

Secrets are encrypted with the shared ``token_vault`` Fernet keyring, so a key
rotation covers file share credentials alongside OAuth tokens. Nothing in this
module ever returns a plaintext secret to an admin caller — ``resolve_secret``
is the single read path and is only reachable from the agent-authenticated
credential endpoint.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.smb_agent import SmbAgent
from app.models.smb_credential import AUTH_METHODS, SmbCredential
from app.models.smb_share import SmbShare
from app.schemas.smb import SmbCredentialCreate, SmbCredentialUpdate
from app.services.token_vault import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

# Auth methods that carry a password. Kerberos uses the agent host's ticket
# cache / keytab and guest needs no secret at all.
PASSWORD_AUTH_METHODS = {"ntlm"}


def _uuid(val) -> uuid.UUID | None:
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    text = str(val).strip()
    if not text:
        return None
    return uuid.UUID(text)


class SmbCredentialError(ValueError):
    """Raised for caller-fixable credential problems (400/404-shaped)."""


def _validate(auth_method: str, username: str | None, password: str | None) -> None:
    if auth_method not in AUTH_METHODS:
        raise SmbCredentialError(
            f"auth_method must be one of {', '.join(AUTH_METHODS)}"
        )
    if auth_method in PASSWORD_AUTH_METHODS:
        if not username:
            raise SmbCredentialError("username is required for NTLM credentials")
        if not password:
            raise SmbCredentialError("password is required for NTLM credentials")


class SmbCredentialService:
    async def create_credential(
        self,
        db: AsyncSession,
        tenant_id: str,
        data: SmbCredentialCreate,
        user_id: str | None = None,
    ) -> SmbCredential:
        await set_tenant_context(db, tenant_id)
        _validate(data.auth_method, data.username, data.password)

        if data.agent_id:
            await self._require_agent(db, tenant_id, data.agent_id)

        existing = await db.execute(
            select(SmbCredential).where(
                SmbCredential.tenant_id == _uuid(tenant_id),
                SmbCredential.name == data.name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise SmbCredentialError(f"A credential named '{data.name}' already exists")

        credential = SmbCredential(
            tenant_id=_uuid(tenant_id),
            name=data.name,
            auth_method=data.auth_method,
            domain=data.domain or None,
            username=data.username or None,
            encrypted_password=(
                encrypt_token(data.password) if data.password else None
            ),
            agent_id=_uuid(data.agent_id),
            created_by_user_id=_uuid(user_id),
        )
        db.add(credential)
        await db.flush()
        logger.info(
            "smb credential created tenant=%s credential=%s method=%s",
            tenant_id,
            credential.id,
            credential.auth_method,
        )
        return credential

    async def list_credentials(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> list[SmbCredential]:
        await set_tenant_context(db, tenant_id)
        result = await db.execute(
            select(SmbCredential)
            .where(SmbCredential.tenant_id == _uuid(tenant_id))
            .order_by(SmbCredential.name)
        )
        return list(result.scalars().all())

    async def share_counts(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict[str, int]:
        """Return ``{credential_id: share_count}`` for the tenant."""
        await set_tenant_context(db, tenant_id)
        result = await db.execute(
            select(SmbShare.credential_id, func.count(SmbShare.id))
            .where(
                SmbShare.tenant_id == _uuid(tenant_id),
                SmbShare.credential_id.is_not(None),
            )
            .group_by(SmbShare.credential_id)
        )
        return {str(row[0]): int(row[1]) for row in result.all()}

    async def get_credential(
        self,
        db: AsyncSession,
        credential_id: str,
        tenant_id: str,
    ) -> SmbCredential:
        await set_tenant_context(db, tenant_id)
        result = await db.execute(
            select(SmbCredential).where(
                SmbCredential.id == _uuid(credential_id),
                SmbCredential.tenant_id == _uuid(tenant_id),
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            raise SmbCredentialError("Credential not found")
        return credential

    async def update_credential(
        self,
        db: AsyncSession,
        credential_id: str,
        tenant_id: str,
        data: SmbCredentialUpdate,
    ) -> SmbCredential:
        credential = await self.get_credential(db, credential_id, tenant_id)

        auth_method = data.auth_method or credential.auth_method
        username = data.username if data.username is not None else credential.username
        # A password is only rewritten when the caller sends one; the existing
        # ciphertext otherwise stays untouched (the UI never round-trips it).
        password_supplied = bool(data.password)
        has_password = password_supplied or bool(credential.encrypted_password)
        _validate(auth_method, username, "unchanged" if has_password else None)

        if data.agent_id is not None:
            if data.agent_id == "":
                credential.agent_id = None
            else:
                await self._require_agent(db, tenant_id, data.agent_id)
                credential.agent_id = _uuid(data.agent_id)

        if data.name is not None and data.name != credential.name:
            clash = await db.execute(
                select(SmbCredential).where(
                    SmbCredential.tenant_id == _uuid(tenant_id),
                    SmbCredential.name == data.name,
                    SmbCredential.id != credential.id,
                )
            )
            if clash.scalar_one_or_none() is not None:
                # Without this the unique constraint surfaces as a 500 at flush.
                raise SmbCredentialError(
                    f"A credential named '{data.name}' already exists"
                )
            credential.name = data.name
        credential.auth_method = auth_method
        if data.domain is not None:
            credential.domain = data.domain or None
        if data.username is not None:
            credential.username = data.username or None
        if password_supplied:
            credential.encrypted_password = encrypt_token(data.password)
            # A new secret invalidates the previous verification result.
            credential.last_verified_at = None
            credential.last_verify_status = None
            credential.last_verify_error = None
        if auth_method not in PASSWORD_AUTH_METHODS and not password_supplied:
            credential.encrypted_password = None
        if data.is_active is not None:
            credential.is_active = data.is_active

        await db.flush()
        return credential

    async def delete_credential(
        self,
        db: AsyncSession,
        credential_id: str,
        tenant_id: str,
    ) -> int:
        """Delete a credential, detaching it from any share that used it.

        Returns the number of shares left without a credential so the caller
        can warn the admin that those shares now fall back to the agent's own
        identity.
        """
        credential = await self.get_credential(db, credential_id, tenant_id)

        count_result = await db.execute(
            select(func.count(SmbShare.id)).where(
                SmbShare.credential_id == credential.id,
                SmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        affected = int(count_result.scalar_one())

        await db.delete(credential)
        await db.flush()
        logger.info(
            "smb credential deleted tenant=%s credential=%s detached_shares=%d",
            tenant_id,
            credential_id,
            affected,
        )
        return affected

    async def record_verification(
        self,
        db: AsyncSession,
        credential_id: str | None,
        tenant_id: str,
        ok: bool,
        error: str | None = None,
    ) -> None:
        if not credential_id:
            return
        try:
            credential = await self.get_credential(db, credential_id, tenant_id)
        except SmbCredentialError:
            return
        credential.last_verified_at = datetime.now(timezone.utc)
        credential.last_verify_status = "ok" if ok else "failed"
        credential.last_verify_error = None if ok else (error or "")[:2000]
        await db.flush()

    async def resolve_secret(
        self,
        db: AsyncSession,
        credential_id: str | uuid.UUID | None,
        tenant_id: str,
        agent_id: str | None = None,
    ) -> dict | None:
        """Return the plaintext credential for an agent, or None.

        This is the only path that decrypts a stored secret. A credential
        pinned to another agent is refused, as is an inactive one.
        """
        if not credential_id:
            return None

        credential = await self.get_credential(db, str(credential_id), tenant_id)
        if not credential.is_active:
            logger.warning(
                "smb credential %s is inactive; agent %s gets no secret",
                credential.id,
                agent_id,
            )
            return None
        if (
            credential.agent_id
            and agent_id
            and str(credential.agent_id) != str(agent_id)
        ):
            logger.warning(
                "smb credential %s is pinned to agent %s; refused for agent %s",
                credential.id,
                credential.agent_id,
                agent_id,
            )
            return None

        password = None
        if credential.encrypted_password:
            try:
                password = decrypt_token(credential.encrypted_password)
            except Exception:
                logger.error(
                    "smb credential %s cannot be decrypted with the configured keyring",
                    credential.id,
                )
                return None

        credential.last_delivered_at = datetime.now(timezone.utc)
        await db.flush()

        return {
            "credential_id": str(credential.id),
            "name": credential.name,
            "auth_method": credential.auth_method,
            "domain": credential.domain,
            "username": credential.username,
            "password": password,
        }

    async def _require_agent(
        self, db: AsyncSession, tenant_id: str, agent_id: str
    ) -> None:
        result = await db.execute(
            select(SmbAgent).where(
                SmbAgent.id == _uuid(agent_id),
                SmbAgent.tenant_id == _uuid(tenant_id),
            )
        )
        if result.scalar_one_or_none() is None:
            raise SmbCredentialError("Agent not found")


smb_credential_service = SmbCredentialService()
