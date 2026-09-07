"""Plan, reconcile, and explicitly cut over cloud-provider bindings."""

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cloud_metadata import CloudMetadata
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.storage_migration import StorageMigration, StorageMigrationMatch
from app.models.tenant import Tenant, TenantSettings
from app.services.storage_discovery import StorageDiscovery
from app.database import set_tenant_context

ACTIVE_PHASES = {"planning", "reconciling", "awaiting_confirmation", "cutover"}
PROVIDERS = {"google_drive", "onedrive", "sharepoint"}
ID_SUFFIX = re.compile(r"\(([0-9a-fA-F]{8})\)\s*$")


def _text(value: Any) -> str:
    return str(value or "").strip().lower().replace("\\", "/").strip("/")


def _document_names(doc) -> set[str]:
    """Match displayed names and the portal's collision-safe physical name."""
    names = {_text(doc.filename)}
    try:
        identity = uuid.UUID(str(doc.id)).hex
    except (TypeError, ValueError):
        return names
    stem, extension = os.path.splitext(doc.filename)
    stored_stem = stem.encode("utf-8")[:180].decode("utf-8", errors="ignore")
    names.add(_text(f"{identity}_{stored_stem}{extension}"))
    return names


def _matter_folder(matter: Matter, provider: str) -> dict:
    folder = matter.cloud_folder if isinstance(matter.cloud_folder, dict) else {}
    return folder.get(provider) if isinstance(folder.get(provider), dict) else {}


def _storage_provider(provider: str) -> str:
    return "google" if provider == "google_drive" else "microsoft"


def _provider_values(provider: str) -> tuple[str, ...]:
    return (provider,)


def _metadata_provider(provider: str) -> str:
    return "google" if provider == "google_drive" else "microsoft"


def _folder_binding(item: dict) -> dict:
    """Normalize discovery metadata to the shape upload paths consume."""
    return {
        **item,
        "id": item.get("id"),
        "matter_folder_id": item.get("id"),
        "folder_id": item.get("id"),
        "folder_name": item.get("name"),
        "folder_url": item.get("url"),
        "path": item.get("path") or item.get("name", ""),
    }


class StorageMigrationService:
    def __init__(self, discovery: StorageDiscovery | None = None) -> None:
        self.discovery = discovery or StorageDiscovery()

    async def start(
        self,
        db: AsyncSession,
        tenant_id: str,
        target_provider: str,
        operator_id: str | None = None,
        evidence_version: str | None = None,
        target_root: dict | None = None,
    ) -> StorageMigration:
        if target_provider not in PROVIDERS:
            raise ValueError(f"Unsupported cloud provider: {target_provider}")
        tenant = (
            await db.execute(
                select(Tenant).where(Tenant.id == tenant_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not tenant:
            raise ValueError("Tenant not found")
        settings = (
            await db.execute(
                select(TenantSettings)
                .where(TenantSettings.tenant_id == tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        source = settings.primary_cloud_provider if settings else None
        if not source:
            raise ValueError("Tenant has no configured source cloud provider")
        if source == target_provider:
            raise ValueError("Target provider is already primary")
        active = (
            (
                await db.execute(
                    select(StorageMigration)
                    .where(
                        StorageMigration.tenant_id == tenant_id,
                        StorageMigration.phase.in_(ACTIVE_PHASES),
                    )
                    .order_by(StorageMigration.started_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if active:
            raise ValueError("A storage migration is already in progress")
        server_evidence = uuid.uuid4().hex
        resolved_target_root = target_root or (
            (tenant.cloud_root_folder or {}).get(target_provider, {}) if tenant else {}
        )
        if not resolved_target_root.get("id") and not resolved_target_root.get(
            "folder_id"
        ):
            raise ValueError("Target provider root must be bound before migration")
        if target_provider == "sharepoint" and not resolved_target_root.get("drive_id"):
            raise ValueError("SharePoint target root must include drive_id")
        migration = StorageMigration(
            tenant_id=tenant_id,
            source_provider=source,
            target_provider=target_provider,
            phase="planning",
            operator_id=operator_id,
            evidence_version=server_evidence,
            previous_root=(tenant.cloud_root_folder or {}) if tenant else {},
            target_root=resolved_target_root,
            bucket_counts={"matched": 0, "missing": 0, "ambiguous": 0},
        )
        db.add(migration)
        await db.flush()
        return migration

    async def reconcile(
        self,
        db: AsyncSession,
        migration_id: str,
        *,
        target_items: list[dict] | None = None,
    ) -> StorageMigration:
        migration_hint = (
            await db.execute(
                select(StorageMigration).where(StorageMigration.id == migration_id)
            )
        ).scalar_one_or_none()
        if not migration_hint:
            raise ValueError("Migration is unavailable for reconciliation")
        hint_tenant_id = migration_hint.tenant_id
        hint_target = migration_hint.target_provider
        hint_root = dict(migration_hint.target_root or {})
        if migration_hint.phase not in {
            "planning",
            "reconciling",
            "awaiting_confirmation",
        }:
            raise ValueError("Migration is unavailable for reconciliation")
        preview_items = target_items
        if preview_items is None:
            preview_items = await self.discovery.discover(
                db,
                str(hint_tenant_id),
                hint_target,
                hint_root,
            )
        await set_tenant_context(db, str(hint_tenant_id))
        await db.execute(
            select(Tenant).where(Tenant.id == hint_tenant_id).with_for_update()
        )
        migration = (
            await db.execute(
                select(StorageMigration)
                .where(StorageMigration.id == migration_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not migration or migration.phase not in {
            "planning",
            "reconciling",
            "awaiting_confirmation",
        }:
            raise ValueError("Migration is unavailable for reconciliation")
        if (
            migration.target_provider != hint_target
            or migration.target_root != hint_root
        ):
            raise ValueError(
                "Migration target changed during discovery; reconcile again"
            )
        migration.phase = "reconciling"
        migration.evidence_version = uuid.uuid4().hex
        matters = (
            (
                await db.execute(
                    select(Matter)
                    .where(Matter.tenant_id == migration.tenant_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        target_items = preview_items
        await db.execute(
            delete(StorageMigrationMatch).where(
                StorageMigrationMatch.migration_id == migration.id
            )
        )
        counts = {"matched": 0, "missing": 0, "ambiguous": 0}
        proposals = [
            (matter, *self._match_matter(matter, target_items, migration.tenant_id))
            for matter in matters
        ]
        target_uses = {}
        for _, candidates, _ in proposals:
            for candidate in candidates:
                target_uses[candidate["id"]] = target_uses.get(candidate["id"], 0) + 1
        for matter, candidates, rung in proposals:
            if len(candidates) == 1 and target_uses[candidates[0]["id"]] > 1:
                candidates = candidates + [candidates[0]]
            if len(candidates) != 1:
                bucket = "missing" if not candidates else "ambiguous"
                counts[bucket] += 1
                db.add(
                    StorageMigrationMatch(
                        migration_id=migration.id,
                        tenant_id=migration.tenant_id,
                        object_type="matter",
                        object_id=str(matter.id),
                        bucket=bucket,
                        matching_rung=rung,
                        source_ref=_matter_folder(matter, migration.source_provider),
                        evidence={"candidate_count": len(candidates)},
                    )
                )
                await self._reconcile_documents(
                    db, migration, matter, None, target_items, counts
                )
                continue
            target = candidates[0]
            counts["matched"] += 1
            db.add(
                StorageMigrationMatch(
                    migration_id=migration.id,
                    tenant_id=migration.tenant_id,
                    object_type="matter",
                    object_id=str(matter.id),
                    bucket="matched",
                    matching_rung=rung,
                    source_ref=_matter_folder(matter, migration.source_provider),
                    target_ref=_folder_binding(target),
                    evidence={"candidate_count": 1},
                )
            )
            await self._reconcile_documents(
                db, migration, matter, target, target_items, counts
            )
        migration.bucket_counts = counts
        migration.phase = "awaiting_confirmation"
        await db.flush()
        return migration

    @staticmethod
    def _match_matter(
        matter: Matter, items: list[dict], tenant_id: uuid.UUID
    ) -> tuple[list[dict], str | None]:
        folders = [i for i in items if i.get("is_folder", True)]

        def eligible(item):
            marker = item.get("marker")
            if marker is None:
                return True
            if not isinstance(marker, dict):
                return False
            return (
                marker.get("schema_version") == 1
                and str(marker.get("tenant_id")) == str(tenant_id)
                and str(marker.get("matter_id")) == str(matter.id)
            )

        folders = [i for i in folders if eligible(i)]
        marker_matches = [
            i
            for i in folders
            if isinstance(i.get("marker"), dict)
            and str(i["marker"].get("tenant_id")) == str(tenant_id)
            and str(i["marker"].get("matter_id")) == str(matter.id)
        ]
        if marker_matches:
            return marker_matches, "marker"
        suffix = f"{str(matter.id).replace('-', '')[:8]}".lower()
        id_matches = [
            i
            for i in folders
            if (m := ID_SUFFIX.search(str(i.get("name", ""))))
            and m.group(1).lower() == suffix
        ]
        if id_matches:
            return id_matches, "id_suffix"
        canonical = (
            _text((matter.cloud_folder or {}).get("path"))
            if isinstance(matter.cloud_folder, dict)
            else ""
        )
        if not canonical:
            canonical = _text(f"claritylegal-records/{getattr(matter, 'slug', '')}")
        path_matches = [
            i
            for i in folders
            if _text(i.get("path")) == canonical
            or _text(i.get("name")) == canonical.rsplit("/", 1)[-1]
        ]
        return path_matches, "canonical_path" if path_matches else None

    async def _reconcile_documents(
        self, db, migration, matter, target_folder, target_items, counts
    ):
        docs = (
            (
                await db.execute(
                    select(MatterDocument).where(
                        MatterDocument.tenant_id == migration.tenant_id,
                        MatterDocument.matter_id == matter.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        docs = [
            doc
            for doc in docs
            if doc.storage_backend in _provider_values(migration.source_provider)
        ]
        descendants = set()
        if target_folder:
            frontier = {str(target_folder.get("id"))}
            while frontier:
                descendants |= frontier
                frontier = {
                    str(i.get("id"))
                    for i in target_items
                    if str(i.get("parent_id")) in frontier
                }
                frontier -= descendants
        children = [
            i
            for i in target_items
            if str(i.get("parent_id")) in descendants and not i.get("is_folder")
        ]
        proposals = []
        for doc in docs:
            sha = (doc.document_sha256 or "").lower()
            matches = [
                i for i in children if sha and str(i.get("sha256", "")).lower() == sha
            ]
            rung = "checksum" if matches else None
            if not matches:
                matches = [
                    i
                    for i in children
                    if (not sha or not i.get("sha256"))
                    and _text(i.get("name")) in _document_names(doc)
                    and doc.file_size is not None
                    and i.get("size") is not None
                    and int(i.get("size")) == int(doc.file_size)
                ]
                rung = "filename_size" if matches else None
            proposals.append((doc, matches, rung))
        target_uses = {}
        for _, candidates, _ in proposals:
            for candidate in candidates:
                target_uses[candidate["id"]] = target_uses.get(candidate["id"], 0) + 1
        for doc, matches, rung in proposals:
            if len(matches) == 1 and target_uses[matches[0]["id"]] > 1:
                matches = matches + [matches[0]]
            bucket = (
                "matched"
                if len(matches) == 1
                else ("missing" if not matches else "ambiguous")
            )
            counts[bucket] += 1
            target = matches[0] if len(matches) == 1 else None
            db.add(
                StorageMigrationMatch(
                    migration_id=migration.id,
                    tenant_id=migration.tenant_id,
                    object_type="document",
                    object_id=str(doc.id),
                    bucket=bucket,
                    matching_rung=rung,
                    source_ref={
                        "provider_object_id": doc.provider_object_id,
                        "provider_drive_id": doc.provider_drive_id,
                        "provider_parent_id": doc.provider_parent_id,
                        "provider_checksum": doc.provider_checksum,
                        "provider_etag": doc.provider_etag,
                        "provider_version_id": doc.provider_version_id,
                        "document_sha256": doc.document_sha256,
                        "filename": doc.filename,
                        "size": doc.file_size,
                        "backend": doc.storage_backend,
                    },
                    target_ref=target,
                    evidence={"candidate_count": len(matches)},
                )
            )

    async def cutover(
        self,
        db: AsyncSession,
        migration_id: str,
        *,
        operator_id: str | None = None,
        evidence_version: str | None = None,
        acknowledged_policy: str | None = None,
    ) -> StorageMigration:
        hint = (
            await db.execute(
                select(StorageMigration).where(StorageMigration.id == migration_id)
            )
        ).scalar_one_or_none()
        if not hint:
            raise ValueError("Migration must be reconciled before cutover")
        tenant = (
            await db.execute(
                select(Tenant)
                .where(Tenant.id == hint.tenant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        migration = (
            await db.execute(
                select(StorageMigration)
                .where(StorageMigration.id == migration_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not migration or migration.phase != "awaiting_confirmation":
            raise ValueError("Migration must be reconciled before cutover")
        if not tenant:
            raise ValueError("Tenant not found")
        if not evidence_version or migration.evidence_version != evidence_version:
            raise ValueError("Migration evidence changed; reconcile again")
        counts = migration.bucket_counts or {}
        blocked = int(counts.get("missing", 0)) + int(counts.get("ambiguous", 0))
        if blocked:
            raise ValueError(
                "Cutover is blocked until every matter and document is resolved"
            )
        audit_rows = (
            (
                await db.execute(
                    select(StorageMigrationMatch).where(
                        StorageMigrationMatch.migration_id == migration.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if any(
            row.bucket != "matched" or not (row.target_ref or {}).get("id")
            for row in audit_rows
        ):
            raise ValueError(
                "Cutover is blocked until every matter and document is resolved"
            )
        row_by_id = {
            str(row.object_id): row
            for row in audit_rows
            if row.object_type == "document"
        }
        current_docs = (
            (
                await db.execute(
                    select(MatterDocument)
                    .where(
                        MatterDocument.tenant_id == migration.tenant_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        current_docs = [
            doc
            for doc in current_docs
            if doc.storage_backend in _provider_values(migration.source_provider)
        ]
        if {str(doc.id) for doc in current_docs} != set(row_by_id):
            raise ValueError("Documents changed since reconciliation; reconcile again")
        for doc in current_docs:
            expected = row_by_id[str(doc.id)].source_ref or {}
            current_source = {
                "provider_object_id": doc.provider_object_id,
                "provider_drive_id": doc.provider_drive_id,
                "provider_parent_id": doc.provider_parent_id,
                "provider_checksum": doc.provider_checksum,
                "provider_etag": doc.provider_etag,
                "provider_version_id": doc.provider_version_id,
                "document_sha256": doc.document_sha256,
                "filename": doc.filename,
                "size": doc.file_size,
                "backend": doc.storage_backend,
            }
            if expected != current_source:
                raise ValueError("A source document pointer changed; reconcile again")
        matter_rows = {
            str(row.object_id): row for row in audit_rows if row.object_type == "matter"
        }
        current_matters = (
            (
                await db.execute(
                    select(Matter)
                    .where(Matter.tenant_id == migration.tenant_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        if {str(m.id) for m in current_matters} != set(matter_rows):
            raise ValueError("Matter set changed since reconciliation; reconcile again")
        for matter in current_matters:
            row = matter_rows.get(str(matter.id))
            if not row or (row.source_ref or {}) != _matter_folder(
                matter, migration.source_provider
            ):
                raise ValueError(
                    "A source matter folder binding changed; reconcile again"
                )
        settings = (
            await db.execute(
                select(TenantSettings)
                .where(TenantSettings.tenant_id == migration.tenant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not settings or settings.primary_cloud_provider != migration.source_provider:
            raise ValueError(
                "Primary provider changed since migration started; abandon and restart"
            )
        migration.phase = "cutover"
        rows = (
            (
                await db.execute(
                    select(StorageMigrationMatch).where(
                        StorageMigrationMatch.migration_id == migration.id,
                        StorageMigrationMatch.object_type == "document",
                        StorageMigrationMatch.bucket == "matched",
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            doc = await db.get(MatterDocument, row.object_id)
            if not doc or not row.target_ref:
                continue
            target = row.target_ref
            doc.storage_provider = _storage_provider(migration.target_provider)
            doc._storage_backend = migration.target_provider
            doc.provider_object_id = target.get("id")
            doc.provider_parent_id = target.get("parent_id")
            doc.provider_drive_id = target.get("drive_id")
            doc.provider_checksum = target.get("sha256")
            doc.provider_etag = target.get("etag")
            doc.provider_version_id = target.get("version_id")
            doc.storage_path = target.get("url")
        for row in matter_rows.values():
            if row.bucket != "matched" or not row.target_ref:
                continue
            matter = await db.get(Matter, row.object_id)
            if not matter:
                continue
            folders = dict(matter.cloud_folder or {})
            folders[migration.target_provider] = row.target_ref
            matter.cloud_folder = folders
        await db.execute(
            delete(CloudMetadata).where(
                CloudMetadata.tenant_id == migration.tenant_id,
                CloudMetadata.provider == _metadata_provider(migration.source_provider),
            )
        )
        settings = (
            await db.execute(
                select(TenantSettings)
                .where(TenantSettings.tenant_id == migration.tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if settings:
            settings.primary_cloud_provider = migration.target_provider
        roots = dict(tenant.cloud_root_folder or {})
        if migration.target_root:
            roots[migration.target_provider] = migration.target_root
            tenant.cloud_root_folder = roots
        from app.models.storage_migration import OnboardingRootAudit

        if tenant.cloud_root_folder:
            db.add(
                OnboardingRootAudit(
                    tenant_id=migration.tenant_id,
                    root=migration.previous_root,
                    action="provider_cutover",
                    actor_id=operator_id or migration.operator_id,
                )
            )
        migration.operator_id = operator_id or migration.operator_id
        migration.acknowledged_policy = acknowledged_policy
        migration.completed_at = datetime.now(timezone.utc)
        migration.needs_reindex = True
        migration.phase = "complete"
        await db.flush()
        return migration

    async def abandon(
        self, db: AsyncSession, migration_id: str, operator_id: str | None = None
    ) -> StorageMigration:
        migration = (
            await db.execute(
                select(StorageMigration)
                .where(StorageMigration.id == migration_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not migration or migration.phase in {"complete", "abandoned"}:
            raise ValueError("Migration is not active")
        migration.phase = "abandoned"
        migration.operator_id = operator_id or migration.operator_id
        migration.completed_at = datetime.now(timezone.utc)
        await db.flush()
        return migration

    # Explicit names used by router/integration callers.
    start_migration = start
    reconcile_migration = reconcile
    cutover_migration = cutover


storage_migration = StorageMigrationService()


async def get_active_migration(
    db: AsyncSession, tenant_id: str
) -> StorageMigration | None:
    """Return the current migration, if any, for settings-write guards."""
    return (
        (
            await db.execute(
                select(StorageMigration)
                .where(
                    StorageMigration.tenant_id == tenant_id,
                    StorageMigration.phase.in_(ACTIVE_PHASES),
                )
                .order_by(StorageMigration.started_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def assert_provider_change_allowed(
    db: AsyncSession, tenant_id: str, target_provider: str
) -> None:
    """Guard direct ``primary_cloud_provider`` writes during migration.

    The settings router can call this before changing the field.  Migration
    endpoints remain the only path that may change it while state is active.
    """
    await db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    active = await get_active_migration(db, tenant_id)
    if active:
        raise ValueError(
            "Complete or abandon the active storage migration before changing provider"
        )
    completed = (
        await db.execute(
            select(StorageMigration)
            .where(
                StorageMigration.tenant_id == tenant_id,
                StorageMigration.phase == "complete",
            )
            .order_by(StorageMigration.completed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if completed and completed.target_provider != target_provider:
        raise ValueError(
            "Use storage migration to change provider after a completed cutover"
        )
