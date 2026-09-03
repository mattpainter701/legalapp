"""Fail-closed native identity, matter-policy, and source authorization."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.matter_assignment import MatterAssignment
from app.models.native_identity import NativeIdentityMapping
from app.models.plugin import Matter

_SID = re.compile(r"^S-\d+(?:-\d+)+$", re.IGNORECASE)
_WELL_KNOWN = ("S-1-1-0", "S-1-5-11")  # Everyone, Authenticated Users
settings = get_settings()


class NativeAuthorizationError(ValueError):
    """Authorization state is missing, stale, or mismatched."""


@dataclass(frozen=True)
class ResolvedNativeIdentity:
    tenant_id: str
    user_id: str
    principal_sids: tuple[str, ...]
    version: int
    provider: str


def expand_effective_group_sids(
    primary_sid: str,
    direct_group_sids: list[str],
    group_graph: dict[str, list[str]],
    *,
    max_principals: int = 4096,
) -> tuple[str, ...]:
    """Expand nested AD groups with cycle and size guards.

    Directory adapters supply immutable SID edges.  Any malformed SID or
    excessive graph fails the entire resolution instead of returning a partial
    principal set that could accidentally omit a deny group.
    """
    pending = [primary_sid, *direct_group_sids, *_WELL_KNOWN]
    resolved: set[str] = set()
    while pending:
        sid = str(pending.pop()).upper()
        if not _SID.fullmatch(sid):
            raise NativeAuthorizationError("directory group expansion is invalid")
        if sid in resolved:
            continue
        resolved.add(sid)
        if len(resolved) > max_principals:
            raise NativeAuthorizationError("directory group expansion is too large")
        pending.extend(group_graph.get(sid, []))
    return tuple(sorted(resolved))


async def resolve_native_identity(
    db: AsyncSession, tenant_id: str, user_id: str, *, now: datetime | None = None
) -> ResolvedNativeIdentity:
    tenant_uuid, user_uuid = uuid.UUID(tenant_id), uuid.UUID(user_id)
    row = await db.scalar(
        select(NativeIdentityMapping).where(
            NativeIdentityMapping.tenant_id == tenant_uuid,
            NativeIdentityMapping.user_id == user_uuid,
        )
    )
    current = now or datetime.now(timezone.utc)
    if row is None or row.state != "healthy":
        raise NativeAuthorizationError("native identity is not healthy")
    if row.expires_at is None or row.expires_at <= current:
        raise NativeAuthorizationError("native identity is stale")
    if (
        row.resolved_at is None
        or row.resolved_at > current
        or (current - row.resolved_at).total_seconds()
        > settings.FIRM_MEMORY_IDENTITY_MAX_AGE_SECONDS
    ):
        raise NativeAuthorizationError("native identity is stale")
    sids = [row.primary_sid, *(row.effective_sids or []), *_WELL_KNOWN]
    if len(sids) > 4096 or any(not _SID.fullmatch(str(sid)) for sid in sids):
        raise NativeAuthorizationError("native identity SID set is invalid")
    normalized = tuple(sorted({str(sid).upper() for sid in sids}))
    if row.primary_sid.upper() not in normalized:
        raise NativeAuthorizationError("native identity SID set is invalid")
    return ResolvedNativeIdentity(
        tenant_id=tenant_id,
        user_id=user_id,
        principal_sids=normalized,
        version=row.version,
        provider=row.provider,
    )


async def require_matter_authorization(
    db: AsyncSession, tenant_id: str, user_id: str, matter_id: str
) -> Matter:
    """Return a tenant matter only when its restriction policy permits the user.

    Existing unrestricted matters remain tenant-wide.  A restricted/ethical-wall
    matter opts in through ``plugin_workflow_state.security_policy`` and then
    requires an explicit ownership or assignment relationship.
    """
    tenant_uuid, user_uuid, matter_uuid = map(
        uuid.UUID, (tenant_id, user_id, matter_id)
    )
    matter = await db.scalar(
        select(Matter).where(Matter.id == matter_uuid, Matter.tenant_id == tenant_uuid)
    )
    if matter is None:
        raise NativeAuthorizationError("matter is unavailable")
    policy = (matter.plugin_workflow_state or {}).get("security_policy") or {}
    restricted = bool(policy.get("restricted") or policy.get("ethical_wall"))
    if not restricted:
        return matter
    explicitly_allowed = {
        str(value) for value in (policy.get("allowed_user_ids") or [])
    }
    relationship = await db.scalar(
        select(Matter.id)
        .outerjoin(
            MatterAssignment,
            and_(
                MatterAssignment.matter_id == Matter.id,
                MatterAssignment.tenant_id == tenant_uuid,
                MatterAssignment.user_id == user_uuid,
            ),
        )
        .where(
            Matter.id == matter_uuid,
            or_(
                Matter.user_id == user_uuid,
                Matter.attorney_of_record_id == user_uuid,
                Matter.partner_attorney_id == user_uuid,
                MatterAssignment.id.is_not(None),
            ),
        )
    )
    if relationship is None and user_id not in explicitly_allowed:
        # Deliberately indistinguishable from a missing matter.
        raise NativeAuthorizationError("matter is unavailable")
    return matter


async def authorized_matter_ids(
    db: AsyncSession, tenant_id: str, user_id: str, matter_ids: list[str]
) -> set[uuid.UUID]:
    """Return the subset of matters this user may search, in two queries.

    This applies exactly the rules in :func:`require_matter_authorization`: an
    unrestricted matter stays tenant-wide, and a restricted or ethical-wall
    matter needs ownership, an assignment, or an explicit allow. It exists so a
    firm-wide search does not issue two round trips per candidate matter. A
    matter this function cannot positively authorize is simply not returned.
    """
    tenant_uuid, user_uuid = uuid.UUID(tenant_id), uuid.UUID(user_id)
    candidates: list[uuid.UUID] = []
    for value in matter_ids:
        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            continue
        if parsed not in candidates:
            candidates.append(parsed)
    if not candidates:
        return set()

    matters = (
        (
            await db.execute(
                select(Matter).where(
                    Matter.tenant_id == tenant_uuid, Matter.id.in_(candidates)
                )
            )
        )
        .scalars()
        .all()
    )
    related = {
        row
        for row in (
            await db.execute(
                select(Matter.id)
                .outerjoin(
                    MatterAssignment,
                    and_(
                        MatterAssignment.matter_id == Matter.id,
                        MatterAssignment.tenant_id == tenant_uuid,
                        MatterAssignment.user_id == user_uuid,
                    ),
                )
                .where(
                    Matter.tenant_id == tenant_uuid,
                    Matter.id.in_(candidates),
                    or_(
                        Matter.user_id == user_uuid,
                        Matter.attorney_of_record_id == user_uuid,
                        Matter.partner_attorney_id == user_uuid,
                        MatterAssignment.id.is_not(None),
                    ),
                )
            )
        ).scalars()
    }

    allowed: set[uuid.UUID] = set()
    for matter in matters:
        policy = (matter.plugin_workflow_state or {}).get("security_policy") or {}
        if not bool(policy.get("restricted") or policy.get("ethical_wall")):
            allowed.add(matter.id)
            continue
        explicitly_allowed = {
            str(value) for value in (policy.get("allowed_user_ids") or [])
        }
        if matter.id in related or user_id in explicitly_allowed:
            allowed.add(matter.id)
    return allowed
