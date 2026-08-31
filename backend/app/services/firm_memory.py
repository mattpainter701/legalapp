"""Safe orchestration for the normalized Firm Memory search contract."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.firm_memory import (
    FirmMemoryCollection,
    FirmMemoryCollectionSource,
    FirmMemoryDocumentMatter,
    FirmMemoryDocumentWorkspace,
    FirmMemorySource,
)
from app.models.matter_smb_share import MatterSmbShare
from app.models.research_workspace import ResearchWorkspaceMember
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_share import SmbShare
from app.models.user import User
from app.schemas.firm_memory import (
    FirmMemoryDocumentSearchHit,
    FirmMemoryDocumentSearchRequest,
    FirmMemoryDocumentSearchResponse,
    FirmMemoryResultProvenance,
    FirmMemorySourceCoverage,
    FirmMemorySourceInfo,
)
from app.services.firm_memory_authorization import (
    FirmMemoryAuthorizationError,
    firm_memory_authorization,
)
from app.services.rbac_service import get_user_capabilities

logger = logging.getLogger(__name__)
settings = get_settings()


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _normalize_windows(value: str | None) -> str:
    return str(value or "").replace("/", "\\").rstrip("\\").casefold()


def _path_is_within(path: str, root: str) -> bool:
    candidate = _normalize_windows(path)
    boundary = _normalize_windows(root)
    return bool(boundary) and (
        candidate == boundary or candidate.startswith(boundary + "\\")
    )


class FirmMemorySearchService:
    async def search(
        self,
        db: AsyncSession,
        *,
        user: User,
        request: FirmMemoryDocumentSearchRequest,
    ) -> FirmMemoryDocumentSearchResponse:
        tenant_id = uuid.UUID(str(user.tenant_id))
        await set_tenant_context(db, str(tenant_id))
        capabilities = await get_user_capabilities(db, user.id)
        await firm_memory_authorization.require_actor(db, user, tenant_id, capabilities)

        matter_ids = self._parse_ids(request.matter_ids, "matter")
        matter_decisions = await firm_memory_authorization.authorize_matters(
            db,
            user=user,
            tenant_id=tenant_id,
            matter_ids=matter_ids,
        )
        if any(not decision.allowed for decision in matter_decisions.values()):
            # Do not disclose whether a rejected id exists or is ethical-wall restricted.
            raise FirmMemoryAuthorizationError(
                "One or more matter filters are unavailable"
            )

        sources, selected_ids = await self._resolve_sources(db, tenant_id, request)
        collection_map = await self._collection_map(
            db, tenant_id, [source.id for source in sources]
        )
        audit_id = request.audit_correlation_id or secrets.token_urlsafe(12)
        coverage: list[FirmMemorySourceCoverage] = []
        results: list[FirmMemoryDocumentSearchHit] = []

        for source in sources:
            decision = await firm_memory_authorization.authorize_source(
                db,
                user=user,
                source=source,
                matter_decisions=matter_decisions,
            )
            explicitly_selected = source.id in selected_ids
            if not decision.allowed:
                # "all" is the set of authorized sources, not a tenant catalog.
                if explicitly_selected:
                    coverage.append(
                        FirmMemorySourceCoverage(
                            source_id=str(source.id),
                            state="unauthorized",
                            authorization=decision.state.value,
                            reason=decision.reason,
                        )
                    )
                continue

            source_coverage = FirmMemorySourceCoverage(
                source_id=str(source.id),
                source_name=source.display_name,
                source_kind=source.source_kind,
                state=source.coverage_state,
                authorization="allowed",
                partial=source.coverage_state != "ready",
            )
            if not matter_ids and not settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED:
                source_coverage.state = "unsupported"
                source_coverage.partial = True
                source_coverage.reason = "generalized_search_rollout_disabled"
                coverage.append(source_coverage)
                continue

            if source.coverage_state in {"offline", "unsupported"}:
                source_coverage.partial = True
                source_coverage.reason = f"source_{source.coverage_state}"
                coverage.append(source_coverage)
                continue

            if (
                source.source_kind == "smb"
                and source.authorization_mode == "matter"
                and source.legacy_smb_share_id
                and matter_ids
            ):
                hits = await self._search_matter_bound_smb(
                    db,
                    tenant_id=tenant_id,
                    requesting_user_id=user.id,
                    source=source,
                    matter_ids=matter_ids,
                    request=request,
                    collection_ids=collection_map.get(source.id, []),
                )
                source_coverage.searched = True
                source_coverage.result_count = len(hits)
                results.extend(hits)
            else:
                # A source-level allow is not document-level ACL trimming.
                # Until a connector contributes an authorized result adapter,
                # returning current SaaS metadata here would be unsafe.
                source_coverage.state = "unsupported"
                source_coverage.partial = True
                source_coverage.reason = (
                    "native_document_authorization_required"
                    if source.authorization_mode == "native"
                    or source.source_kind == "smb"
                    else "source_search_adapter_unavailable"
                )
            coverage.append(source_coverage)

        results.sort(key=lambda hit: hit.score or 0.0, reverse=True)
        results = results[: request.limit]
        partial = any(item.partial or not item.searched for item in coverage)
        complete = (
            bool(coverage)
            and not partial
            and all(item.state == "ready" for item in coverage)
        )
        logger.info(
            "Firm Memory search completed",
            extra={
                "firm_memory_audit_correlation_id": audit_id,
                "firm_memory_tenant_id": str(tenant_id),
                "firm_memory_user_id": str(user.id),
                "firm_memory_source_count": len(coverage),
                "firm_memory_result_count": len(results),
                "firm_memory_partial": partial,
            },
        )
        return FirmMemoryDocumentSearchResponse(
            audit_correlation_id=audit_id,
            results=results,
            coverage=coverage,
            partial=partial,
            complete=complete,
            generalized_search_enabled=settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED,
        )

    async def list_sources(
        self,
        db: AsyncSession,
        *,
        user: User,
        matter_id_values: list[str],
    ) -> list[FirmMemorySourceInfo]:
        tenant_id = uuid.UUID(str(user.tenant_id))
        await set_tenant_context(db, str(tenant_id))
        capabilities = await get_user_capabilities(db, user.id)
        await firm_memory_authorization.require_actor(db, user, tenant_id, capabilities)
        matter_ids = self._parse_ids(matter_id_values, "matter")
        matter_decisions = await firm_memory_authorization.authorize_matters(
            db, user=user, tenant_id=tenant_id, matter_ids=matter_ids
        )
        if any(not decision.allowed for decision in matter_decisions.values()):
            raise FirmMemoryAuthorizationError(
                "One or more matter filters are unavailable"
            )
        sources = list(
            (
                await db.execute(
                    select(FirmMemorySource)
                    .where(FirmMemorySource.tenant_id == tenant_id)
                    .order_by(FirmMemorySource.display_name)
                )
            ).scalars()
        )
        collections = await self._collection_map(
            db, tenant_id, [source.id for source in sources]
        )
        visible: list[FirmMemorySourceInfo] = []
        for source in sources:
            decision = await firm_memory_authorization.authorize_source(
                db,
                user=user,
                source=source,
                matter_decisions=matter_decisions,
            )
            if not decision.allowed:
                continue
            if not matter_ids and not settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED:
                continue
            visible.append(
                FirmMemorySourceInfo(
                    id=str(source.id),
                    display_name=source.display_name,
                    source_kind=source.source_kind,
                    provider_key=source.provider_key,
                    share_id=(
                        str(source.legacy_smb_share_id)
                        if source.legacy_smb_share_id
                        else None
                    ),
                    coverage_state=source.coverage_state,
                    collection_ids=collections.get(source.id, []),
                )
            )
        return visible

    @staticmethod
    def _parse_ids(values: list[str], label: str) -> tuple[uuid.UUID, ...]:
        parsed: list[uuid.UUID] = []
        for value in values:
            item = _uuid(value)
            if item is None:
                raise ValueError(f"Invalid {label} id")
            if item not in parsed:
                parsed.append(item)
        return tuple(parsed)

    async def _resolve_sources(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        request: FirmMemoryDocumentSearchRequest,
    ) -> tuple[list[FirmMemorySource], set[uuid.UUID]]:
        requested_source_ids = set(self._parse_ids(request.source_ids, "source"))
        collection_ids = set(self._parse_ids(request.collection_ids, "collection"))
        collection_source_ids: set[uuid.UUID] = set()
        if collection_ids:
            collection_source_ids.update(
                (
                    await db.execute(
                        select(FirmMemoryCollectionSource.source_id)
                        .join(
                            FirmMemoryCollection,
                            FirmMemoryCollection.id
                            == FirmMemoryCollectionSource.collection_id,
                        )
                        .where(
                            FirmMemoryCollectionSource.tenant_id == tenant_id,
                            FirmMemoryCollection.tenant_id == tenant_id,
                            FirmMemoryCollectionSource.collection_id.in_(
                                collection_ids
                            ),
                            FirmMemoryCollection.is_enabled.is_(True),
                        )
                    )
                ).scalars()
            )
        selected_ids = requested_source_ids | collection_source_ids
        stmt = select(FirmMemorySource).where(FirmMemorySource.tenant_id == tenant_id)
        if request.source_scope == "selected":
            stmt = stmt.where(FirmMemorySource.id.in_(selected_ids))
        elif request.source_scope == "on_prem":
            stmt = stmt.where(FirmMemorySource.source_kind == "smb")
        elif request.source_scope == "cloud":
            stmt = stmt.where(FirmMemorySource.source_kind == "cloud")
        if request.source_scope != "selected" and selected_ids:
            stmt = stmt.where(FirmMemorySource.id.in_(selected_ids))
        sources = list(
            (await db.execute(stmt.order_by(FirmMemorySource.display_name))).scalars()
        )
        return sources, selected_ids

    @staticmethod
    async def _collection_map(
        db: AsyncSession, tenant_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        if not source_ids:
            return {}
        rows = (
            await db.execute(
                select(
                    FirmMemoryCollectionSource.source_id,
                    FirmMemoryCollectionSource.collection_id,
                )
                .join(
                    FirmMemoryCollection,
                    FirmMemoryCollection.id == FirmMemoryCollectionSource.collection_id,
                )
                .where(
                    FirmMemoryCollectionSource.tenant_id == tenant_id,
                    FirmMemoryCollection.tenant_id == tenant_id,
                    FirmMemoryCollectionSource.source_id.in_(source_ids),
                    FirmMemoryCollection.is_enabled.is_(True),
                )
            )
        ).all()
        result: dict[uuid.UUID, list[str]] = {}
        for source_id, collection_id in rows:
            result.setdefault(source_id, []).append(str(collection_id))
        return result

    async def _search_matter_bound_smb(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        requesting_user_id: uuid.UUID,
        source: FirmMemorySource,
        matter_ids: tuple[uuid.UUID, ...],
        request: FirmMemoryDocumentSearchRequest,
        collection_ids: list[str],
    ) -> list[FirmMemoryDocumentSearchHit]:
        share = await db.scalar(
            select(SmbShare).where(
                SmbShare.id == source.legacy_smb_share_id,
                SmbShare.tenant_id == tenant_id,
                SmbShare.is_enabled.is_(True),
            )
        )
        if share is None:
            return []
        bindings = list(
            (
                await db.execute(
                    select(MatterSmbShare).where(
                        MatterSmbShare.tenant_id == tenant_id,
                        MatterSmbShare.share_id == share.id,
                        MatterSmbShare.matter_id.in_(matter_ids),
                    )
                )
            ).scalars()
        )
        if not bindings:
            return []

        scope_filters = []
        binding_roots: list[tuple[uuid.UUID, str]] = []
        for binding in bindings:
            root = share.share_path.rstrip("\\/")
            if binding.folder_path:
                root += "\\" + binding.folder_path.replace("/", "\\").strip("\\")
            binding_roots.append((binding.matter_id, root))
            escaped = root.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            scope_filters.append(
                or_(
                    SmbFileIndex.path.ilike(escaped, escape="!"),
                    SmbFileIndex.path.ilike(escaped + "!\\%", escape="!"),
                )
            )

        ts_query = func.plainto_tsquery("english", request.query)
        stmt = select(
            SmbFileIndex,
            func.ts_rank(SmbFileIndex.search_vector, ts_query).label("score"),
        ).where(
            SmbFileIndex.tenant_id == tenant_id,
            SmbFileIndex.share_id == share.id,
            SmbFileIndex.is_deleted.is_(False),
            SmbFileIndex.search_vector.op("@@")(ts_query),
            or_(*scope_filters),
        )
        filters = request.filters
        if filters.file_extensions:
            stmt = stmt.where(SmbFileIndex.ext.in_(filters.file_extensions))
        if filters.mime_types:
            stmt = stmt.where(SmbFileIndex.mime_type.in_(filters.mime_types))
        if filters.modified_from:
            stmt = stmt.where(SmbFileIndex.modified_time >= filters.modified_from)
        if filters.modified_to:
            stmt = stmt.where(SmbFileIndex.modified_time <= filters.modified_to)
        stmt = stmt.order_by(
            func.ts_rank(SmbFileIndex.search_vector, ts_query).desc()
        ).limit(request.limit)
        rows = (await db.execute(stmt)).all()

        document_keys = [str(row[0].id) for row in rows]
        generic_matters, workspaces = await self._document_associations(
            db,
            tenant_id=tenant_id,
            user_id=requesting_user_id,
            source_id=source.id,
            document_keys=document_keys,
            allowed_matter_ids=matter_ids,
        )
        hits: list[FirmMemoryDocumentSearchHit] = []
        for item, score in rows:
            matched_matters = [
                str(matter_id)
                for matter_id, root in binding_roots
                if _path_is_within(item.path, root)
            ]
            matched_matters.extend(
                matter_id
                for matter_id in generic_matters.get(str(item.id), [])
                if matter_id not in matched_matters
            )
            relative = item.path
            if _path_is_within(item.path, share.share_path):
                relative = item.path[len(share.share_path.rstrip("\\/")) :].lstrip(
                    "\\/"
                )
            hits.append(
                FirmMemoryDocumentSearchHit(
                    document_id=self._opaque_document_id(source.id, item.id),
                    title=item.filename,
                    snippet=item.snippet,
                    score=float(score) if score is not None else None,
                    mime_type=item.mime_type,
                    file_extension=item.ext,
                    size_bytes=item.size_bytes,
                    modified_at=item.modified_time,
                    matter_ids=matched_matters,
                    workspace_ids=workspaces.get(str(item.id), []),
                    provenance=FirmMemoryResultProvenance(
                        source_id=str(source.id),
                        source_name=source.display_name,
                        source_kind=source.source_kind,
                        collection_ids=collection_ids,
                        index_kind="smb_metadata_fts",
                        indexed_at=item.last_seen_at,
                        relative_location=relative,
                    ),
                )
            )
        return hits

    @staticmethod
    async def _document_associations(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        source_id: uuid.UUID,
        document_keys: list[str],
        allowed_matter_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        if not document_keys:
            return {}, {}
        matter_rows = (
            await db.execute(
                select(
                    FirmMemoryDocumentMatter.document_key,
                    FirmMemoryDocumentMatter.matter_id,
                ).where(
                    FirmMemoryDocumentMatter.tenant_id == tenant_id,
                    FirmMemoryDocumentMatter.source_id == source_id,
                    FirmMemoryDocumentMatter.document_key.in_(document_keys),
                    FirmMemoryDocumentMatter.matter_id.in_(allowed_matter_ids),
                )
            )
        ).all()
        workspace_rows = (
            await db.execute(
                select(
                    FirmMemoryDocumentWorkspace.document_key,
                    FirmMemoryDocumentWorkspace.workspace_id,
                )
                .join(
                    ResearchWorkspaceMember,
                    (
                        ResearchWorkspaceMember.tenant_id
                        == FirmMemoryDocumentWorkspace.tenant_id
                    )
                    & (
                        ResearchWorkspaceMember.workspace_id
                        == FirmMemoryDocumentWorkspace.workspace_id
                    ),
                )
                .where(
                    FirmMemoryDocumentWorkspace.tenant_id == tenant_id,
                    FirmMemoryDocumentWorkspace.source_id == source_id,
                    FirmMemoryDocumentWorkspace.document_key.in_(document_keys),
                    ResearchWorkspaceMember.user_id == user_id,
                    ResearchWorkspaceMember.revoked_at.is_(None),
                )
            )
        ).all()
        matters: dict[str, list[str]] = {}
        workspaces: dict[str, list[str]] = {}
        for key, matter_id in matter_rows:
            matters.setdefault(key, []).append(str(matter_id))
        for key, workspace_id in workspace_rows:
            workspaces.setdefault(key, []).append(str(workspace_id))
        return matters, workspaces

    @staticmethod
    def _opaque_document_id(source_id: uuid.UUID, native_id: uuid.UUID) -> str:
        digest = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            f"firm-memory:v1:{source_id}:{native_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"fmdoc_{digest}"


firm_memory_search_service = FirmMemorySearchService()
