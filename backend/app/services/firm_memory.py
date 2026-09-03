"""Safe orchestration for the normalized Firm Memory search contract."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

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
    FirmMemoryResultAction,
    FirmMemoryResultProvenance,
    FirmMemorySourceCoverage,
    FirmMemorySourceInfo,
)
from app.services.firm_memory_authorization import (
    AuthorizationDecision,
    FirmMemoryAuthorizationError,
    firm_memory_authorization,
)
from app.services.rbac_service import get_user_capabilities

logger = logging.getLogger(__name__)
settings = get_settings()

# A matterless search expands into the actor's own authorized matters. The cap
# keeps one query bounded on firms with large matter counts; exceeding it is
# reported as partial coverage rather than silently trimmed.
MATTER_SCOPE_EXPANSION_CAP = 100

# The SaaS-side SMB index holds filenames and a capped preview, not document
# text. Coverage must never let that read as a full-text corpus search.
SMB_METADATA_INDEX_KIND = "smb_metadata_fts"


@dataclass(frozen=True, slots=True)
class _MatterBoundSmbSearchResult:
    hits: list[FirmMemoryDocumentSearchHit]
    state: Literal["offline", "unsupported"] | None = None
    reason: str | None = None

    @property
    def searched(self) -> bool:
        return self.state is None and self.reason is None


@dataclass(frozen=True, slots=True)
class _MatterScope:
    """The matter filter actually applied to one source for one search."""

    matter_ids: tuple[uuid.UUID, ...]
    decisions: dict[uuid.UUID, AuthorizationDecision]
    expanded: bool = False
    truncated: bool = False


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

        started = time.monotonic()
        sources, selected_ids = await self._resolve_sources(db, tenant_id, request)
        collection_map = await self._collection_map(
            db, tenant_id, [source.id for source in sources]
        )
        audit_id = request.audit_correlation_id or secrets.token_urlsafe(12)
        coverage: list[FirmMemorySourceCoverage] = []
        results: list[FirmMemoryDocumentSearchHit] = []

        for source in sources:
            explicitly_selected = source.id in selected_ids
            scope = await self._matter_scope_for_source(
                db,
                user=user,
                tenant_id=tenant_id,
                source=source,
                requested_matter_ids=matter_ids,
                requested_decisions=matter_decisions,
            )
            if scope.expanded and not scope.matter_ids:
                # The actor holds no authorized matter on this share. Report
                # that the search did not cover it, without naming a source
                # this actor is not entitled to see.
                coverage.append(
                    FirmMemorySourceCoverage(
                        source_id=str(source.id),
                        state="unauthorized",
                        authorization="denied",
                        partial=True,
                        reason="no_authorized_matter_scope",
                    )
                )
                continue

            decision = await firm_memory_authorization.authorize_source(
                db,
                user=user,
                source=source,
                matter_decisions=scope.decisions,
            )
            if not decision.allowed:
                # "all" is the set of authorized sources, not a tenant catalog.
                # A source the actor could search after choosing a matter is
                # still reported, so a matterless search is never a silent zero.
                if explicitly_selected or decision.reason == "matter_scope_required":
                    coverage.append(
                        FirmMemorySourceCoverage(
                            source_id=str(source.id),
                            state="unauthorized",
                            authorization=decision.state.value,
                            partial=True,
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
                matter_scope_count=len(scope.matter_ids),
            )
            if not scope.matter_ids and not settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED:
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
                and scope.matter_ids
            ):
                adapter_result = await self._search_matter_bound_smb(
                    db,
                    tenant_id=tenant_id,
                    requesting_user_id=user.id,
                    source=source,
                    matter_ids=scope.matter_ids,
                    request=request,
                    collection_ids=collection_map.get(source.id, []),
                )
                if adapter_result.state is not None:
                    source_coverage.state = adapter_result.state
                source_coverage.searched = adapter_result.searched
                source_coverage.partial = (
                    source_coverage.partial or not adapter_result.searched
                )
                source_coverage.reason = adapter_result.reason
                source_coverage.result_count = len(adapter_result.hits)
                if adapter_result.searched:
                    # Filenames and a capped preview are not document text, so
                    # this can never be reported as complete corpus coverage.
                    source_coverage.index_kind = SMB_METADATA_INDEX_KIND
                    source_coverage.partial = True
                    if scope.truncated:
                        source_coverage.reason = "matter_scope_truncated"
                results.extend(adapter_result.hits)
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
        # A response that searched nothing is not a complete "no matches", and
        # it is not un-partial either.  Both flags must say so.
        partial = not coverage or any(
            item.partial or not item.searched for item in coverage
        )
        complete = (
            bool(coverage)
            and not partial
            and all(item.state == "ready" for item in coverage)
        )
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "Firm Memory search completed",
            extra={
                "firm_memory_audit_correlation_id": audit_id,
                "firm_memory_tenant_id": str(tenant_id),
                "firm_memory_user_id": str(user.id),
                "firm_memory_source_count": len(coverage),
                "firm_memory_result_count": len(results),
                "firm_memory_partial": partial,
                "firm_memory_duration_ms": duration_ms,
            },
        )
        return FirmMemoryDocumentSearchResponse(
            audit_correlation_id=audit_id,
            results=results,
            coverage=coverage,
            partial=partial,
            complete=complete,
            generalized_search_enabled=settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED,
            coverage_message=self._coverage_message(coverage, complete=complete),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _coverage_message(
        coverage: list[FirmMemorySourceCoverage], *, complete: bool
    ) -> str | None:
        """Say in one sentence why a response is not complete.

        Coverage reasons are machine tokens; a lawyer deciding whether "no
        results" means "not in the corpus" needs the sentence.
        """
        if complete:
            return None
        if not coverage:
            return (
                "No source could be searched for this query. Ask an "
                "administrator to configure and authorize a Firm Memory source."
            )
        reasons = {item.reason for item in coverage if item.reason}
        if "matter_scope_required" in reasons:
            return (
                "Some authorized sources are bound to matters and were not "
                "searched. Choose a matter to include them."
            )
        if "no_authorized_matter_scope" in reasons:
            return (
                "You are not authorized on any matter bound to one or more of "
                "these sources, so they were not searched."
            )
        if "generalized_search_rollout_disabled" in reasons:
            return (
                "Firm-wide search is not enabled for this firm yet. Choose a "
                "matter to search its bound file shares."
            )
        if "matter_scope_truncated" in reasons:
            return (
                f"Only the first {MATTER_SCOPE_EXPANSION_CAP} of your "
                "authorized matters were searched. Choose a matter to narrow "
                "this search."
            )
        if any(item.index_kind == SMB_METADATA_INDEX_KIND for item in coverage):
            return (
                "On-premises results come from the filename and preview index, "
                "not full document text. Open a matter's Firm Memory search to "
                "search inside documents."
            )
        if "native_document_authorization_required" in reasons:
            return (
                "One or more sources need per-user file permissions before "
                "they can be searched firm-wide."
            )
        return (
            "This response does not cover every authorized source. Review the "
            "source states before treating it as complete."
        )

    async def _matter_scope_for_source(
        self,
        db: AsyncSession,
        *,
        user: User,
        tenant_id: uuid.UUID,
        source: FirmMemorySource,
        requested_matter_ids: tuple[uuid.UUID, ...],
        requested_decisions: dict[uuid.UUID, AuthorizationDecision],
    ) -> _MatterScope:
        """Resolve the matter filter this source is searched under.

        An explicit filter always wins. Without one, a matter-bound share is
        searched across the matters this actor is already authorized on, which
        widens nothing: every candidate is decided by the same matter policy a
        typed filter would go through.
        """
        if requested_matter_ids or not settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED:
            return _MatterScope(
                matter_ids=requested_matter_ids, decisions=requested_decisions
            )
        if not (
            source.is_enabled
            and source.source_kind == "smb"
            and source.authorization_mode == "matter"
            and source.legacy_smb_share_id
        ):
            return _MatterScope(
                matter_ids=requested_matter_ids, decisions=requested_decisions
            )

        candidates = tuple(
            dict.fromkeys(
                (
                    await db.execute(
                        select(MatterSmbShare.matter_id)
                        .where(
                            MatterSmbShare.tenant_id == tenant_id,
                            MatterSmbShare.share_id == source.legacy_smb_share_id,
                        )
                        .order_by(MatterSmbShare.matter_id)
                        .limit(MATTER_SCOPE_EXPANSION_CAP + 1)
                    )
                ).scalars()
            )
        )
        truncated = len(candidates) > MATTER_SCOPE_EXPANSION_CAP
        candidates = candidates[:MATTER_SCOPE_EXPANSION_CAP]
        decisions = await firm_memory_authorization.authorize_matter_scope(
            db, user=user, tenant_id=tenant_id, matter_ids=candidates
        )
        allowed = {
            matter_id: decision
            for matter_id, decision in decisions.items()
            if decision.allowed
        }
        return _MatterScope(
            matter_ids=tuple(allowed),
            decisions=allowed,
            expanded=True,
            truncated=truncated,
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
            # The filter list must offer the same sources a search would
            # actually reach, otherwise a matter-bound share is invisible until
            # the user guesses that it needs a matter first.
            scope = await self._matter_scope_for_source(
                db,
                user=user,
                tenant_id=tenant_id,
                source=source,
                requested_matter_ids=matter_ids,
                requested_decisions=matter_decisions,
            )
            if scope.expanded and not scope.matter_ids:
                continue
            decision = await firm_memory_authorization.authorize_source(
                db,
                user=user,
                source=source,
                matter_decisions=scope.decisions,
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
    ) -> _MatterBoundSmbSearchResult:
        share = await db.scalar(
            select(SmbShare).where(
                SmbShare.id == source.legacy_smb_share_id,
                SmbShare.tenant_id == tenant_id,
                SmbShare.is_enabled.is_(True),
            )
        )
        if share is None:
            return _MatterBoundSmbSearchResult(
                hits=[],
                state="offline",
                reason="smb_share_unavailable",
            )
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
            return _MatterBoundSmbSearchResult(
                hits=[],
                state="unsupported",
                reason="matter_smb_binding_unavailable",
            )

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
                        index_kind=SMB_METADATA_INDEX_KIND,
                        indexed_at=item.last_seen_at,
                        relative_location=relative,
                    ),
                    actions=self._matter_bound_actions(
                        matter_id=matched_matters[0] if matched_matters else None,
                        native_file_id=item.id,
                    ),
                )
            )
        return _MatterBoundSmbSearchResult(hits=hits)

    @staticmethod
    def _matter_bound_actions(
        *, matter_id: str | None, native_file_id: uuid.UUID
    ) -> list[FirmMemoryResultAction]:
        """Issue the result actions the server can stand behind.

        The deep link carries no path and no permission of its own: the target
        resolver re-checks the tenant, the matter binding, the live index row
        and the bound folder before it will show anything. Opening the file on
        a workstation needs a signed intent that only that resolver can mint,
        so this states why the action is unavailable instead of hiding it.
        """
        if matter_id is None:
            return [
                FirmMemoryResultAction(
                    kind="lawhand_result",
                    label="Open LawHand result",
                    available=False,
                    reason="result_is_not_bound_to_an_authorized_matter",
                ),
            ]
        href = (
            f"/firm-memory?matter={quote(str(matter_id), safe='')}"
            f"&file={quote(str(native_file_id), safe='')}"
        )
        return [
            FirmMemoryResultAction(
                kind="lawhand_result",
                label="Open LawHand result",
                available=True,
                href=href,
            ),
            FirmMemoryResultAction(
                kind="open_on_device",
                label="Open on this computer",
                available=False,
                reason="open_from_the_lawhand_result_page",
            ),
        ]

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
