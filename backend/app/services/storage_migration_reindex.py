"""Durable, metadata-only reindex after a cloud storage cutover."""

from __future__ import annotations

import logging
import uuid
from dateutil import parser as dateutil_parser
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.storage_migration import StorageMigration
from app.models.cloud_metadata import CloudMetadata
from app.database import set_tenant_context
from app.services.storage_discovery import StorageDiscovery

logger = logging.getLogger(__name__)


def cloud_metadata_provider(provider: str) -> str:
    return "google" if provider == "google_drive" else "microsoft"


def inventory_metadata(item: dict[str, Any], provider: str) -> dict[str, Any] | None:
    """Convert discovery metadata to CloudMetadata fields; omit folders/markers."""
    if item.get("is_folder") or item.get("name") == ".lawhand-matter.json":
        return None
    if provider == "sharepoint" and item.get("id") and not item.get("drive_id"):
        raise ValueError("SharePoint inventory is missing its drive identity")
    return (
        {
            "provider": cloud_metadata_provider(provider),
            "object_type": "sharepoint_file" if provider == "sharepoint" else "file",
            "object_id": f"{item['drive_id']}:{item['id']}"
            if provider == "sharepoint"
            else item.get("id"),
            "title": item.get("name") or "",
            "path": item.get("path") or item.get("name") or "",
            "mime_type": item.get("mime_type") or item.get("mimeType"),
            "size_bytes": item.get("size"),
            "web_url": item.get("url") or item.get("webUrl"),
            "parent_id": item.get("parent_id"),
            "snippet": item.get("name") or "",
            "modified_time": _parse_time(item.get("modified_time")),
        }
        if item.get("id")
        else None
    )


class StorageMigrationReindexService:
    def __init__(self, discovery: StorageDiscovery | None = None) -> None:
        self.discovery = discovery or StorageDiscovery()

    async def run(
        self, db: AsyncSession, tenant_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Run the latest pending completed migration, durably clearing its flag."""
        tenant_uuid = uuid.UUID(str(tenant_id))
        await set_tenant_context(db, str(tenant_uuid))
        migration = (
            await db.execute(
                select(StorageMigration)
                .where(
                    StorageMigration.tenant_id == tenant_uuid,
                    StorageMigration.phase == "complete",
                )
                .order_by(StorageMigration.completed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if migration is None or (not force and not migration.needs_reindex):
            return {"status": "not_needed", "items": 0}
        migration_id = migration.id
        target_provider = migration.target_provider
        target_root = migration.target_root or {}
        try:
            # Discovery happens before the state lock because token refresh may
            # commit its own credential state.
            items = await self.discovery.discover(
                db, str(tenant_uuid), target_provider, target_root
            )
            target_items = [
                metadata
                for item in items
                if (metadata := inventory_metadata(item, target_provider))
            ]
            await set_tenant_context(db, str(tenant_uuid))
            # Match cutover's tenant-first lock order before locking migration.
            from app.models.tenant import Tenant

            await db.execute(
                select(Tenant)
                .where(Tenant.id == tenant_uuid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            migration = (
                await db.execute(
                    select(StorageMigration)
                    .where(
                        StorageMigration.id == migration_id,
                        StorageMigration.tenant_id == tenant_uuid,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            latest = (
                await db.execute(
                    select(StorageMigration)
                    .where(
                        StorageMigration.tenant_id == tenant_uuid,
                        StorageMigration.phase == "complete",
                    )
                    .order_by(StorageMigration.completed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if (
                latest is None
                or latest.id != migration.id
                or (not force and not migration.needs_reindex)
            ):
                return {"status": "superseded", "items": 0}
            from app.services.cloud_sync import CloudSyncService

            sync = CloudSyncService()
            await db.execute(
                delete(CloudMetadata).where(
                    CloudMetadata.tenant_id == tenant_uuid,
                    CloudMetadata.provider == cloud_metadata_provider(target_provider),
                    CloudMetadata.object_type.in_(["file", "sharepoint_file"]),
                )
            )
            for metadata in target_items:
                await sync._upsert(
                    db, str(tenant_uuid), trusted_reindex=True, **metadata
                )
            migration.needs_reindex = False
            migration.error_message = None
            await db.commit()
            return {
                "status": "completed",
                "items": len(target_items),
                "provider": target_provider,
            }
        except Exception as exc:
            await db.rollback()
            try:
                await set_tenant_context(db, str(tenant_uuid))
                migration = (
                    await db.execute(
                        select(StorageMigration)
                        .where(
                            StorageMigration.id == migration_id,
                            StorageMigration.tenant_id == tenant_uuid,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if migration:
                    migration.needs_reindex = True
                    migration.error_message = str(exc)[:2000]
                    await db.commit()
            except Exception:
                await db.rollback()
            logger.warning(
                "storage migration reindex failed for tenant %s: %s", tenant_id, exc
            )
            return {
                "status": "failed",
                "items": 0,
                "error": str(exc),
                "provider": target_provider,
            }


storage_migration_reindex = StorageMigrationReindexService()


def _parse_time(value):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        return value
    try:
        return dateutil_parser.isoparse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
