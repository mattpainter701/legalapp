from app.middleware.rate_limit import _counts_against_tenant_daily


def test_tenant_daily_limit_skips_conversation_reads_and_deletes():
    assert _counts_against_tenant_daily("GET", "/api/conversations") is False
    assert (
        _counts_against_tenant_daily(
            "GET", "/api/conversations/00000000-0000-0000-0000-000000000001"
        )
        is False
    )
    assert (
        _counts_against_tenant_daily(
            "DELETE", "/api/conversations/00000000-0000-0000-0000-000000000001"
        )
        is False
    )


def test_tenant_daily_limit_counts_llm_and_tool_paths():
    assert (
        _counts_against_tenant_daily(
            "POST",
            "/api/conversations/00000000-0000-0000-0000-000000000001/messages",
        )
        is True
    )
    assert (
        _counts_against_tenant_daily(
            "POST",
            "/api/conversations/00000000-0000-0000-0000-000000000001/messages/stream",
        )
        is True
    )
    assert _counts_against_tenant_daily("POST", "/api/plugins/execute") is True
