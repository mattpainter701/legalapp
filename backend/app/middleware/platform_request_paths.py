"""Request paths that carry platform-operator credentials and audit events."""

PLATFORM_PROTECTED_PREFIXES = ("/api/platform", "/api/mcp/authority")


def is_platform_protected_path(path: str) -> bool:
    return path.startswith(PLATFORM_PROTECTED_PREFIXES)
