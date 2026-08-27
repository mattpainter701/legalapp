"""Safe, metadata-driven cloning for disposable demo tenants.

The registry decides which tenant tables are eligible.  This module supplies
the mechanics: pre-allocate fresh UUIDs, rewrite every declared relationship
and JSON-contained identifier, and copy local files beneath the new tenant's
storage root.
"""

from __future__ import annotations

import copy
import re
import shutil
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from sqlalchemy import Table, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import Base, set_tenant_context
from app.services.demo_registry import DEMO_TABLE_REGISTRY, SENSITIVE_NEVER_CLONE


class DemoFixtureError(RuntimeError):
    """The configured fixture cannot be copied safely."""


_FILE_COLUMNS = {
    "storage_path",
    "source_storage_path",
    "output_storage_path",
    "generated_storage_path",
}

_UUID_TOKEN = re.compile(
    r"(?<![0-9A-Fa-f])"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[1-5][0-9A-Fa-f]{3}-"
    r"[89ABab][0-9A-Fa-f]{3}-[0-9A-Fa-f]{12}"
    r"(?![0-9A-Fa-f])"
)


def _clone_tables() -> dict[str, Table]:
    missing = [
        name
        for name, policy in DEMO_TABLE_REGISTRY.items()
        if policy.clone and name not in Base.metadata.tables
    ]
    if missing:
        raise DemoFixtureError(f"Clone tables are not registered: {sorted(missing)}")
    return {
        name: Base.metadata.tables[name]
        for name, policy in DEMO_TABLE_REGISTRY.items()
        if policy.clone
    }


def _required_dependency_order(tables: dict[str, Table]) -> list[str]:
    """Order inserts by non-null FK edges; nullable edges are patched later."""
    dependencies: dict[str, set[str]] = {name: set() for name in tables}
    children: dict[str, set[str]] = defaultdict(set)
    for name, table in tables.items():
        for column in table.columns:
            if column.nullable:
                continue
            for fk in column.foreign_keys:
                parent = fk.column.table.name
                if parent in tables and parent != name:
                    dependencies[name].add(parent)
                    children[parent].add(name)
    ready = deque(sorted(name for name, deps in dependencies.items() if not deps))
    ordered: list[str] = []
    while ready:
        name = ready.popleft()
        ordered.append(name)
        for child in sorted(children[name]):
            dependencies[child].discard(name)
            if not dependencies[child]:
                ready.append(child)
    if len(ordered) != len(tables):
        blocked = sorted(name for name, deps in dependencies.items() if deps)
        raise DemoFixtureError(
            f"Required clone dependencies contain a cycle: {blocked}"
        )
    return ordered


def _remap_embedded(value: Any, id_map: dict[uuid.UUID, uuid.UUID]) -> Any:
    if isinstance(value, uuid.UUID):
        return id_map.get(value, value)
    if isinstance(value, str):
        try:
            source_id = uuid.UUID(value)
        except (ValueError, AttributeError):
            return _UUID_TOKEN.sub(
                lambda match: str(
                    id_map.get(uuid.UUID(match.group(0)), uuid.UUID(match.group(0)))
                ),
                value,
            )
        mapped = id_map.get(source_id)
        return str(mapped) if mapped else value
    if isinstance(value, list):
        return [_remap_embedded(item, id_map) for item in value]
    if isinstance(value, tuple):
        return tuple(_remap_embedded(item, id_map) for item in value)
    if isinstance(value, dict):
        return {key: _remap_embedded(item, id_map) for key, item in value.items()}
    return value


def _safe_file_target(
    source: str,
    *,
    fixture_tenant_id: uuid.UUID,
    target_tenant_id: uuid.UUID,
    copied: dict[Path, Path],
) -> str:
    if source.lower().startswith(("http://", "https://")):
        raise DemoFixtureError("Fixture contains a remote file reference")
    settings = get_settings()
    fixture_root = (Path(settings.UPLOAD_DIR) / str(fixture_tenant_id)).resolve()
    target_root = (Path(settings.UPLOAD_DIR) / str(target_tenant_id)).resolve()
    source_path = Path(source).resolve()
    if not source_path.is_relative_to(fixture_root) or not source_path.is_file():
        raise DemoFixtureError("Fixture file is missing or escapes its tenant root")
    relative = source_path.relative_to(fixture_root)
    target_path = (target_root / relative).resolve()
    if not target_path.is_relative_to(target_root):
        raise DemoFixtureError("Resolved demo file escapes its tenant root")
    if source_path not in copied:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied[source_path] = target_path
    return str(copied[source_path])


async def validate_demo_fixture(db: AsyncSession, fixture_tenant_id: uuid.UUID) -> None:
    """Reject fixture tenants carrying credentials or live integration state."""
    await set_tenant_context(db, str(fixture_tenant_id))
    tenant_settings = Base.metadata.tables["tenant_settings"]
    settings_row = (
        (
            await db.execute(
                select(tenant_settings).where(
                    tenant_settings.c.tenant_id == fixture_tenant_id
                )
            )
        )
        .mappings()
        .first()
    )
    if settings_row and any(
        (
            settings_row["use_customer_llm"],
            settings_row["customer_llm_provider"],
            settings_row["customer_llm_config"],
            settings_row["primary_cloud_provider"],
        )
    ):
        raise DemoFixtureError(
            "Fixture tenant settings contain customer LLM or cloud configuration"
        )
    for table_name in sorted(SENSITIVE_NEVER_CLONE):
        table = Base.metadata.tables[table_name]
        count = await db.scalar(
            select(table.c.tenant_id)
            .where(table.c.tenant_id == fixture_tenant_id)
            .limit(1)
        )
        if count is not None:
            raise DemoFixtureError(
                f"Fixture contains forbidden live state in {table_name}"
            )


async def clone_demo_fixture(
    db: AsyncSession,
    *,
    fixture_tenant_id: uuid.UUID,
    target_tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
) -> dict[str, int]:
    """Clone allowlisted fixture rows into a new tenant in the current transaction."""
    if fixture_tenant_id == target_tenant_id:
        raise DemoFixtureError("Fixture and target tenant must differ")
    tables = _clone_tables()
    await validate_demo_fixture(db, fixture_tenant_id)

    source_rows: dict[str, list[dict[str, Any]]] = {}
    id_map: dict[uuid.UUID, uuid.UUID] = {fixture_tenant_id: target_tenant_id}
    fixture_user_ids: set[uuid.UUID] = set()
    users = Base.metadata.tables["users"]
    fixture_user_ids.update(
        (
            await db.execute(
                select(users.c.id).where(users.c.tenant_id == fixture_tenant_id)
            )
        )
        .scalars()
        .all()
    )
    id_map.update({source_id: target_user_id for source_id in fixture_user_ids})

    for name, table in tables.items():
        rows = (
            await db.execute(
                select(table).where(table.c.tenant_id == fixture_tenant_id)
            )
        ).mappings()
        source_rows[name] = [dict(row) for row in rows]
        pk_columns = list(table.primary_key.columns)
        if len(pk_columns) != 1 or pk_columns[0].name != "id":
            raise DemoFixtureError(
                f"Clone table {name} must have a single id primary key"
            )
        for row in source_rows[name]:
            source_id = row["id"]
            if not isinstance(source_id, uuid.UUID):
                raise DemoFixtureError(
                    f"Clone table {name} does not use UUID identifiers"
                )
            id_map[source_id] = uuid.uuid4()

    copied_files: dict[Path, Path] = {}
    inserted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    nullable_updates: list[tuple[Table, uuid.UUID, dict[str, Any]]] = []
    try:
        await set_tenant_context(db, str(target_tenant_id))
        for name in _required_dependency_order(tables):
            table = tables[name]
            for source_row in source_rows[name]:
                row = {
                    column.name: copy.deepcopy(source_row[column.name])
                    for column in table.columns
                    if column.computed is None
                }
                row["id"] = id_map[source_row["id"]]
                row["tenant_id"] = target_tenant_id
                deferred: dict[str, Any] = {}
                for column in table.columns:
                    if column.computed is not None:
                        continue
                    source_value = source_row.get(column.name)
                    if source_value is None:
                        continue
                    foreign_keys = list(column.foreign_keys)
                    if foreign_keys:
                        fk = foreign_keys[0]
                        parent_table = fk.column.table.name
                        if parent_table == "tenants":
                            mapped = target_tenant_id
                        elif parent_table == "users":
                            mapped = target_user_id
                        elif source_value in id_map:
                            mapped = id_map[source_value]
                        elif column.nullable:
                            mapped = None
                        else:
                            raise DemoFixtureError(
                                f"{name}.{column.name} references data outside the clone registry"
                            )
                        if column.nullable and mapped is not None:
                            deferred[column.name] = mapped
                            row[column.name] = None
                        else:
                            row[column.name] = mapped
                    elif column.name in _FILE_COLUMNS and isinstance(source_value, str):
                        row[column.name] = _safe_file_target(
                            source_value,
                            fixture_tenant_id=fixture_tenant_id,
                            target_tenant_id=target_tenant_id,
                            copied=copied_files,
                        )
                    else:
                        row[column.name] = _remap_embedded(source_value, id_map)
                await db.execute(insert(table).values(**row))
                inserted[name].append(row)
                if deferred:
                    nullable_updates.append((table, row["id"], deferred))

        for table, row_id, values in nullable_updates:
            await db.execute(update(table).where(table.c.id == row_id).values(**values))
        await db.flush()
    except Exception:
        target_root = (
            Path(get_settings().UPLOAD_DIR) / str(target_tenant_id)
        ).resolve()
        upload_root = Path(get_settings().UPLOAD_DIR).resolve()
        if target_root.is_relative_to(upload_root) and target_root.exists():
            shutil.rmtree(target_root)
        raise
    return {name: len(rows) for name, rows in inserted.items()}
