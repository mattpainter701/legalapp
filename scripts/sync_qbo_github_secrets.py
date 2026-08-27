"""Safely upload allow-listed QBO values from a dotenv file to GitHub secrets."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from validate_qbo_env import load_env

ALLOWLIST = ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REDIRECT_URI", "QBO_ENVIRONMENT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    values = load_env(args.env_file)
    selected = {key: values[key] for key in ALLOWLIST if values.get(key)}
    if not selected:
        print("No allow-listed QBO settings found.")
        return 1
    for key in selected:
        print(f"{key}: {'would upload' if not args.apply else 'uploading'}")
        if args.apply:
            subprocess.run(
                ["gh", "secret", "set", key, "--repo", args.repo],
                input=selected[key],
                text=True,
                check=True,
            )
    print(f"{'Uploaded' if args.apply else 'Validated'} {len(selected)} QBO GitHub secret(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())