from app.services import platform_auth


TEST_PLATFORM_SIGNING_KEY = "test-platform-signing-key-xxxxxxxxxxxxxxxxxxxxxxxx"


def platform_headers(scopes: list[str] | None = None) -> dict[str, str]:
    granted = scopes or sorted(platform_auth.PLATFORM_SCOPES)
    platform_auth.settings.PLATFORM_TOKEN_SIGNING_KEY = TEST_PLATFORM_SIGNING_KEY
    token, _, _ = platform_auth.issue_platform_token(
        subject="test-operator",
        scopes=granted,
        allowed_scopes=granted,
    )
    return {"Authorization": f"Bearer {token}"}
