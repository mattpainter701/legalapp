from app.services.llm import LLMService


def test_disclaimer_footer_is_conditional_for_legal_work_only():
    prompt = LLMService()._build_system_prompt(
        tenant_name="Bismarcklaw",
        context="",
        memory_context="",
        user_name="Matt",
    )

    assert "Prepared for Bismarcklaw. Attorney review recommended before reliance." in prompt
    assert "only when the response contains legal analysis" in prompt
    assert "Do not append that footer to ordinary non-legal answers" in prompt
    assert "End every response with" not in prompt
