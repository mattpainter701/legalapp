"""Fail-closed authorization policy for Firm Memory sources and matters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.firm_memory import (
    FirmMemoryMatterGrant,
    FirmMemoryMatterPolicy,
    FirmMemorySource,
    FirmMemorySourceGrant,
)
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter
from app.models.rbac import UserRole
from app.models.user import User


class AuthorizationState(str, Enum):
    ALLOW = "allowed"
    DENY = "denied"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AuthorizationDecision:
    state: AuthorizationState
    reason: str

    @property
    def allowed(self) -> bool:
        return self.state is AuthorizationState.ALLOW


class NativeSourceAuthorizer(Protocol):
    """Provider hook for native source authorization.

    Implementations must validate the actor against the native source for this
    query.  Returning ``UNKNOWN`` (including provider timeout or missing
    identity mapping) is a denial at the service boundary.
    """

    async def authorize_search(
        self,
        *,
        db: AsyncSession,
        user: User,
        source: FirmMemorySource,
        matter_ids: tuple[uuid.UUID, ...],
    ) -> AuthorizationDecision: ...


class NativeAuthorizerRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, NativeSourceAuthorizer] = {}

    def register(self, key: str, provider: NativeSourceAuthorizer) -> None:
        normalized = key.strip().casefold()
        if not normalized:
            raise ValueError("native authorizer key is required")
        self._providers[normalized] = provider

    def get(self, key: str | None) -> NativeSourceAuthorizer | None:
        return self._providers.get((key or "").strip().casefold())


native_authorizers = NativeAuthorizerRegistry()


class FirmMemoryAuthorizationError(PermissionError):
    pass


class FirmMemoryAuthorizationPolicy:
    """Compose tenant, entitlement, matter, source, and native decisions."""

    async def require_actor(
        self,
        db: AsyncSession,
        user: User,
        tenant_id: uuid.UUID,
        capabilities: set[str],
    ) -> None:
        member = await db.scalar(
            select(User.id).where(
                User.id == user.id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        if member is None or user.tenant_id != tenant_id:
            raise FirmMemoryAuthorizationError("Firm Memory access denied")
        if "search_firm_memory" not in capabilities:
            raise FirmMemoryAuthorizationError("Firm Memory access denied")

    async def authorize_matters(
        self,
        db: AsyncSession,
        *,
        user: User,
        tenant_id: uuid.UUID,
        matter_ids: tuple[uuid.UUID, ...],
    ) -> dict[uuid.UUID, AuthorizationDecision]:
        decisions: dict[uuid.UUID, AuthorizationDecision] = {}
        for matter_id in matter_ids:
            decisions[matter_id] = await self._authorize_matter(
                db, user=user, tenant_id=tenant_id, matter_id=matter_id
            )
        return decisions

    async def _authorize_matter(
        self,
        db: AsyncSession,
        *,
        user: User,
        tenant_id: uuid.UUID,
        matter_id: uuid.UUID,
    ) -> AuthorizationDecision:
        exists = await db.scalar(
            select(Matter.id).where(
                Matter.id == matter_id, Matter.tenant_id == tenant_id
            )
        )
        if exists is None:
            return AuthorizationDecision(
                AuthorizationState.DENY, "matter_not_available"
            )

        policy = await db.scalar(
            select(FirmMemoryMatterPolicy).where(
                FirmMemoryMatterPolicy.tenant_id == tenant_id,
                FirmMemoryMatterPolicy.matter_id == matter_id,
            )
        )
        if policy and policy.access_mode == "firm":
            return AuthorizationDecision(AuthorizationState.ALLOW, "firm_matter_policy")

        assigned = await db.scalar(
            select(MatterAssignment.id).where(
                MatterAssignment.tenant_id == tenant_id,
                MatterAssignment.matter_id == matter_id,
                MatterAssignment.user_id == user.id,
            )
        )
        if policy is None:
            # Existing matter assignments are an established authorization
            # signal.  Absence of both policy and assignment is not an allow.
            state = AuthorizationState.ALLOW if assigned else AuthorizationState.UNKNOWN
            return AuthorizationDecision(
                state,
                "legacy_matter_assignment" if assigned else "matter_policy_unknown",
            )
        if policy.access_mode == "assigned":
            return AuthorizationDecision(
                AuthorizationState.ALLOW if assigned else AuthorizationState.DENY,
                "matter_assignment_required",
            )

        explicit = await db.scalar(
            select(FirmMemoryMatterGrant.id).where(
                FirmMemoryMatterGrant.tenant_id == tenant_id,
                FirmMemoryMatterGrant.matter_id == matter_id,
                FirmMemoryMatterGrant.user_id == user.id,
            )
        )
        return AuthorizationDecision(
            AuthorizationState.ALLOW if explicit else AuthorizationState.DENY,
            "restricted_matter_explicit_grant",
        )

    async def authorize_source(
        self,
        db: AsyncSession,
        *,
        user: User,
        source: FirmMemorySource,
        matter_decisions: dict[uuid.UUID, AuthorizationDecision],
    ) -> AuthorizationDecision:
        if not source.is_enabled:
            return AuthorizationDecision(AuthorizationState.DENY, "source_disabled")
        if source.authorization_mode == "firm":
            return AuthorizationDecision(AuthorizationState.ALLOW, "firm_entitlement")
        if source.authorization_mode == "matter":
            if not matter_decisions:
                return AuthorizationDecision(
                    AuthorizationState.UNKNOWN, "matter_scope_required"
                )
            if all(decision.allowed for decision in matter_decisions.values()):
                return AuthorizationDecision(
                    AuthorizationState.ALLOW, "authorized_matter_scope"
                )
            return AuthorizationDecision(AuthorizationState.DENY, "matter_scope_denied")
        if source.authorization_mode == "explicit":
            return await self._explicit_source_decision(db, user=user, source=source)
        if source.authorization_mode == "native":
            provider = native_authorizers.get(source.native_authorizer_key)
            if provider is None:
                return AuthorizationDecision(
                    AuthorizationState.UNKNOWN, "native_authorizer_unavailable"
                )
            try:
                decision = await provider.authorize_search(
                    db=db,
                    user=user,
                    source=source,
                    matter_ids=tuple(matter_decisions),
                )
            except Exception:
                return AuthorizationDecision(
                    AuthorizationState.UNKNOWN, "native_authorizer_error"
                )
            return (
                decision
                if isinstance(decision, AuthorizationDecision)
                else AuthorizationDecision(
                    AuthorizationState.UNKNOWN, "native_authorizer_invalid_response"
                )
            )
        return AuthorizationDecision(
            AuthorizationState.UNKNOWN, "source_policy_unknown"
        )

    async def _explicit_source_decision(
        self, db: AsyncSession, *, user: User, source: FirmMemorySource
    ) -> AuthorizationDecision:
        role_ids = select(UserRole.role_id).where(
            UserRole.tenant_id == source.tenant_id,
            UserRole.user_id == user.id,
        )
        grants = (
            (
                await db.execute(
                    select(FirmMemorySourceGrant.effect).where(
                        FirmMemorySourceGrant.tenant_id == source.tenant_id,
                        FirmMemorySourceGrant.source_id == source.id,
                        (
                            (
                                (FirmMemorySourceGrant.subject_type == "user")
                                & (FirmMemorySourceGrant.subject_id == user.id)
                            )
                            | (
                                (FirmMemorySourceGrant.subject_type == "role")
                                & FirmMemorySourceGrant.subject_id.in_(role_ids)
                            )
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        if "deny" in grants:
            return AuthorizationDecision(
                AuthorizationState.DENY, "explicit_source_deny"
            )
        if "allow" in grants:
            return AuthorizationDecision(
                AuthorizationState.ALLOW, "explicit_source_allow"
            )
        return AuthorizationDecision(
            AuthorizationState.UNKNOWN, "explicit_source_policy_missing"
        )


firm_memory_authorization = FirmMemoryAuthorizationPolicy()
