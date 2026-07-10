"""Build an identity-bound platform bootstrap JSON entry without echoing its key."""

import argparse
import getpass
import hashlib
import json


ALLOWED_SCOPES = {
    "platform:read",
    "platform:write",
    "platform:llm:read",
    "platform:llm:write",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--expires-at", required=True, help="Timezone-aware ISO-8601")
    parser.add_argument(
        "--scopes",
        default="platform:read",
        help="Comma-separated maximum scopes",
    )
    args = parser.parse_args()
    scopes = sorted({value.strip() for value in args.scopes.split(",") if value.strip()})
    if not scopes or set(scopes) - ALLOWED_SCOPES:
        parser.error("invalid scopes")
    raw_key = getpass.getpass("Bootstrap key (will not be echoed): ")
    if len(raw_key) < 32:
        parser.error("bootstrap key must be at least 32 characters")
    print(
        json.dumps(
            {
                "operator_id": args.operator_id,
                "key_hash": hashlib.sha256(raw_key.encode()).hexdigest(),
                "scopes": scopes,
                "expires_at": args.expires_at,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
