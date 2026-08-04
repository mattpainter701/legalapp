#!/usr/bin/env python3
"""Fail CI when a commit weakens migration or tenant-data safety.

The gate intentionally applies only to migrations changed by the commit range:

* deployed revision files are immutable (no edits, deletes, or renames);
* new ``upgrade()`` functions may not remove customer data or constraints;
* broad data rewrites and un-restored RLS relaxations are rejected.

Downgrades are excluded from the destructive-operation scan because their job is
normally to remove the objects created by the matching upgrade.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = Path("backend/migrations/versions")
EMPTY_GIT_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def _resolve_base(base: str) -> str:
    if base and set(base) != {"0"}:
        try:
            _git("cat-file", "-e", f"{base}^{{commit}}")
            return base
        except RuntimeError as exc:
            raise RuntimeError(f"migration diff base is not available: {base}") from exc
    return EMPTY_GIT_TREE


def changed_migrations(base: str, head: str) -> list[tuple[str, Path, Path | None]]:
    """Return ``(status, current_path, old_path)`` for changed revisions."""

    resolved_base = _resolve_base(base)
    revisions = (
        [resolved_base, head]
        if resolved_base == EMPTY_GIT_TREE
        else [f"{resolved_base}...{head}"]
    )
    output = _git(
        "diff",
        "--name-status",
        "--find-renames",
        *revisions,
        "--",
        MIGRATION_ROOT.as_posix(),
    )
    changes: list[tuple[str, Path, Path | None]] = []
    for raw_line in output.splitlines():
        fields = raw_line.split("\t")
        if not fields:
            continue
        status = fields[0]
        if status.startswith("R"):
            if len(fields) != 3:
                raise RuntimeError(f"unexpected git rename record: {raw_line}")
            changes.append((status, Path(fields[2]), Path(fields[1])))
        elif len(fields) == 2:
            changes.append((status, Path(fields[1]), None))
        else:
            raise RuntimeError(f"unexpected git diff record: {raw_line}")
    return changes


def _upgrade_node(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "upgrade"
        ):
            return node
    return None


def _op_call_name(call: ast.Call) -> str | None:
    function = call.func
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "op"
    ):
        return function.attr
    return None


def _string_fragments(node: ast.AST) -> str:
    """Render SQL string expressions while retaining f-string placeholders."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_string_fragments(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return "{" + ast.unparse(node.value) + "}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _string_fragments(node.left) + _string_fragments(node.right)
    if isinstance(node, ast.Call):
        return " ".join(_string_fragments(argument) for argument in node.args)
    return ""


def _sql_without_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"\s+", " ", sql).strip()


def _has_keyword_argument(call: ast.Call, name: str, expected: object) -> bool:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value == expected
    return False


def _sql_findings(path: Path, line: int, sql: str) -> list[Finding]:
    normalized = _sql_without_comments(sql)
    upper = normalized.upper()
    findings: list[Finding] = []

    destructive_patterns = (
        (r"\bTRUNCATE(?:\s+TABLE)?\b", "TRUNCATE is forbidden in an upgrade"),
        (r"\bDROP\s+TABLE\b", "DROP TABLE is forbidden in an upgrade"),
        (
            r"\bALTER\s+TABLE\b.*?\bDROP\s+COLUMN\b",
            "DROP COLUMN is forbidden in an upgrade",
        ),
        (r"\bDELETE\s+FROM\b", "DELETE FROM is forbidden in an upgrade"),
        (
            r"\bALTER\s+TABLE\b.*?\bALTER\s+COLUMN\b.*?\bTYPE\b",
            "in-place column type changes require an expand/contract migration",
        ),
    )
    for pattern, message in destructive_patterns:
        if re.search(pattern, upper, flags=re.DOTALL):
            findings.append(Finding(path, line, message))

    for statement in re.split(r";", upper):
        if re.search(
            r"\bUPDATE\s+[A-Z0-9_.\"{}]+\s+(?:AS\s+[A-Z0-9_]+\s+)?SET\b", statement
        ):
            if not re.search(r"\bWHERE\b", statement):
                findings.append(
                    Finding(
                        path,
                        line,
                        "unscoped UPDATE is forbidden; bound every backfill with a WHERE clause",
                    )
                )
                break

    return findings


def _rls_relaxation_findings(
    path: Path, sql_calls: list[tuple[int, str]]
) -> list[Finding]:
    relaxations = (
        (
            r"ALTER\s+TABLE\s+([A-Z0-9_.\"{}]+)\s+DISABLE\s+ROW\s+LEVEL\s+SECURITY",
            "ENABLE ROW LEVEL SECURITY",
            "RLS is disabled without being restored later in the upgrade",
        ),
        (
            r"ALTER\s+TABLE\s+([A-Z0-9_.\"{}]+)\s+NO\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "FORCE RLS is removed without being restored later in the upgrade",
        ),
    )
    findings: list[Finding] = []
    normalized_calls = [
        (line, _sql_without_comments(statement).upper())
        for line, statement in sorted(sql_calls)
    ]
    for index, (line, statement) in enumerate(normalized_calls):
        later_sql = " ".join(item[1] for item in normalized_calls[index + 1 :])
        for pattern, restore_suffix, message in relaxations:
            for match in re.finditer(pattern, statement):
                table = match.group(1)
                restore = f"ALTER TABLE {table} {restore_suffix}"
                remaining_sql = statement[match.end() :] + " " + later_sql
                if restore not in remaining_sql:
                    findings.append(Finding(path, line, f"{message}: {table}"))
    return findings


def analyze_upgrade_source(source: str, path: Path) -> list[Finding]:
    """Return safety findings for one candidate migration source file."""

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Finding(path, exc.lineno or 1, f"migration does not parse: {exc.msg}")]

    upgrade = _upgrade_node(tree)
    if upgrade is None:
        return [Finding(path, 1, "new migration must define upgrade()")]

    findings: list[Finding] = []
    sql_calls: list[tuple[int, str]] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call):
            continue
        operation = _op_call_name(node)
        if operation in {"drop_table", "drop_column", "drop_constraint"}:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    f"op.{operation} is forbidden in an upgrade; use an expand/contract release",
                )
            )
        elif operation == "alter_column":
            if _has_keyword_argument(node, "nullable", False):
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        "nullable=False can reject existing customer rows; add and backfill in separate releases",
                    )
                )
            if any(keyword.arg == "type_" for keyword in node.keywords):
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        "in-place type_ changes require an expand/contract migration",
                    )
                )
        elif operation == "execute" and node.args:
            statement = _string_fragments(node.args[0])
            sql_calls.append((node.lineno, statement))
            findings.extend(_sql_findings(path, node.lineno, statement))
    findings.extend(_rls_relaxation_findings(path, sql_calls))
    return findings


def evaluate_changes(
    changes: Iterable[tuple[str, Path, Path | None]],
) -> tuple[list[Path], list[Finding]]:
    additions: list[Path] = []
    findings: list[Finding] = []
    for status, current_path, old_path in changes:
        code = status[0]
        if code == "A":
            additions.append(current_path)
            absolute_path = ROOT / current_path
            findings.extend(
                analyze_upgrade_source(
                    absolute_path.read_text(encoding="utf-8"), current_path
                )
            )
            continue

        revision = old_path if code in {"D", "R"} and old_path else current_path
        findings.append(
            Finding(
                revision,
                1,
                "existing migration revisions are immutable; add a new forward migration instead",
            )
        )
    return additions, findings


def _print_finding(finding: Finding) -> None:
    message = (
        finding.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    )
    print(
        f"::error file={finding.path.as_posix()},line={finding.line}::{message}",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="base commit SHA")
    parser.add_argument("--head", default="HEAD", help="candidate commit SHA")
    args = parser.parse_args()

    try:
        changes = changed_migrations(args.base, args.head)
        additions, findings = evaluate_changes(changes)
    except (OSError, RuntimeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            _print_finding(finding)
        print(
            "Migration safety gate failed. Preserve deployed revisions and use additive, "
            "expand/contract migrations for customer data.",
            file=sys.stderr,
        )
        return 1

    if additions:
        print("Safe new migrations: " + ", ".join(path.name for path in additions))
    else:
        print("No migration revisions changed in this commit range.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
