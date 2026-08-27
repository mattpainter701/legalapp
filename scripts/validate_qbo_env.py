"""Validate QuickBooks Online configuration without printing secret values."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

REQUIRED = ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET")
OPTIONAL = ("QBO_REDIRECT_URI", "QBO_ENVIRONMENT")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    if not args.env_file.is_file():
        print(f"ERROR: env file not found: {args.env_file}")
        return 2

    values = load_env(args.env_file)
    errors: list[str] = []
    for key in REQUIRED:
        value = values.get(key, "")
        ok = bool(value) and "replace" not in value.lower() and "your_" not in value.lower()
        print(f"{key}: {'present' if ok else 'MISSING'}")
        if not ok:
            errors.append(f"{key} is missing or looks like a placeholder")

    redirect = values.get("QBO_REDIRECT_URI")
    if redirect:
        parsed = urlparse(redirect)
        valid = parsed.scheme == "https" and parsed.path.endswith("/api/integrations/qbo/callback")
        print(f"QBO_REDIRECT_URI: {'valid' if valid else 'INVALID'}")
        if not valid:
            errors.append("QBO_REDIRECT_URI must be HTTPS and end in /api/integrations/qbo/callback")
    else:
        print("QBO_REDIRECT_URI: not set (application default will be used)")

    environment = values.get("QBO_ENVIRONMENT")
    if environment:
        valid = environment.lower() in {"sandbox", "production"}
        print(f"QBO_ENVIRONMENT: {'valid' if valid else 'INVALID'}")
        if not valid:
            errors.append("QBO_ENVIRONMENT must be sandbox or production")
    else:
        print("QBO_ENVIRONMENT: not set (application default will be used)")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("QBO configuration validation passed; no secret values were displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())