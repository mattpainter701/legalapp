#!/usr/bin/env python3
"""Compute a content hash over the LiteLLM gateway's build inputs.

The hashed set IS whatever litellm/Dockerfile COPYs, plus the Dockerfile
itself and its digest-pinned FROM reference, so the hash can never drift from
the image's real build inputs. Tag images as ``legalapp-litellm:src-<hash>``;
the deploy script treats that tag's existence on the host as proof the
gateway content is already built.

Exits non-zero if the Dockerfile's FROM line is not digest-pinned or a COPY
source cannot be resolved, so an unreviewed Dockerfile change fails closed
instead of silently widening the input set.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

DOCKERFILE_REL = Path("litellm/Dockerfile")
FROM_RE = re.compile(r"^\s*FROM\s+(\S+)")
COPY_RE = re.compile(r"^\s*COPY\s+(.+?)\s*$")
HASH_TRUNCATE = 16


class GatewayHashError(RuntimeError):
    pass


def resolve_inputs(root: Path) -> tuple[str, list[Path]]:
    dockerfile = root / DOCKERFILE_REL
    if not dockerfile.is_file():
        raise GatewayHashError(f"missing gateway Dockerfile: {dockerfile}")
    lines = dockerfile.read_text(encoding="utf-8").splitlines()

    from_refs = [m.group(1) for line in lines if (m := FROM_RE.match(line))]
    if len(from_refs) != 1:
        raise GatewayHashError(
            f"expected exactly one FROM line in {DOCKERFILE_REL}, found {len(from_refs)}"
        )
    if "@sha256:" not in from_refs[0]:
        raise GatewayHashError(
            f"gateway base image is not digest-pinned in {DOCKERFILE_REL}: {from_refs[0]}"
        )

    sources: set[Path] = set()
    for lineno, line in enumerate(lines, start=1):
        match = COPY_RE.match(line)
        if not match:
            continue
        tokens = match.group(1).split()
        if any(token.startswith("--") or token.startswith("[") for token in tokens):
            raise GatewayHashError(
                f"unsupported COPY form at {DOCKERFILE_REL}:{lineno}: {line.strip()}"
            )
        if len(tokens) < 2:
            raise GatewayHashError(
                f"could not parse COPY sources at {DOCKERFILE_REL}:{lineno}: {line.strip()}"
            )
        for source in tokens[:-1]:
            sources.add(Path(source))
    if not sources:
        raise GatewayHashError(f"no COPY inputs found in {DOCKERFILE_REL}")

    resolved = []
    for source in sorted(sources, key=lambda p: p.as_posix()):
        path = root / source
        if not path.is_file():
            raise GatewayHashError(f"COPY source does not exist: {path}")
        resolved.append(path)
    return from_refs[0], resolved


def compute_hash(root: Path) -> tuple[str, list[Path]]:
    from_ref, sources = resolve_inputs(root)
    digest = hashlib.sha256()
    digest.update(b"litellm-src-hash-v1\n")
    digest.update(f"FROM {from_ref}\n".encode("utf-8"))
    dockerfile = root / DOCKERFILE_REL
    digest.update(
        f"{DOCKERFILE_REL.as_posix()} {dockerfile.stat().st_size}\n".encode("utf-8")
    )
    digest.update(dockerfile.read_bytes())
    digest.update(b"\n")
    for path in sources:
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest.update(f"{relative} {len(data)}\n".encode("utf-8"))
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()[:HASH_TRUNCATE], sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root containing litellm/Dockerfile (default: this script's repo)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="also print the resolved input list to stderr",
    )
    args = parser.parse_args(argv)

    try:
        digest, sources = compute_hash(args.root)
    except GatewayHashError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.verify:
        for path in sources:
            print(path.relative_to(args.root).as_posix(), file=sys.stderr)
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
