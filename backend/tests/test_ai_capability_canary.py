import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "canary_ai_capabilities.py"
)
SPEC = importlib.util.spec_from_file_location("canary_ai_capabilities", SCRIPT_PATH)
canary = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(canary)


def test_credential_state_rejects_template_notes_without_exposing_values():
    assert canary.credential_state("") == "missing"
    assert (
        canary.credential_state("configured only on production host")
        == "placeholder_or_note"
    )
    assert canary.credential_state("sk-...") == "placeholder_or_note"
    assert canary.credential_state("CHANGE_ME_PROVIDER_KEY") == "placeholder_or_note"
    assert canary.credential_state("sk-valid-token-without-spaces") == "configured"


def test_local_document_canaries_pass_for_txt_docx_and_pdf():
    results = canary.local_document_canaries()

    assert {result["capability"] for result in results} == {
        "local_txt_extraction",
        "local_docx_extraction",
        "local_pdf_extraction",
    }
    assert all(result["passed"] for result in results)


def test_transcription_canary_uses_multipart_audio_contract(monkeypatch, tmp_path):
    fixture = tmp_path / "canary.wav"
    fixture.write_bytes(b"synthetic-audio")
    observed = {}

    def fake_post_multipart(url, key, fields, file_field, file_path, timeout):
        observed.update(
            url=url,
            key=key,
            fields=fields,
            file_field=file_field,
            file_path=file_path,
            timeout=timeout,
        )
        return 200, {"text": "LawHand canary seven four two"}, 12

    monkeypatch.setattr(canary, "post_multipart", fake_post_multipart)

    result = canary.transcription_canary(
        "https://provider.invalid/audio/transcriptions",
        "secret-test-key",
        "openai/gpt-transcribe",
        fixture,
        "LawHand canary seven four two",
        30,
    )

    assert result["passed"] is True
    assert observed["fields"] == {
        "model": "openai/gpt-transcribe",
        "language": "en",
    }
    assert observed["file_field"] == "file"
    assert observed["file_path"] == fixture


def test_live_evidence_blocks_unconfigured_keys_without_calling_provider(
    monkeypatch, tmp_path
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=configured only on production host\n"
        "DEEPSEEK_API_KEY=\n",
        encoding="utf-8",
    )
    args = canary.parse_args(["--live", "--env-file", str(env_file)])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    evidence = canary.build_evidence(args)

    assert evidence["passed"] is False
    assert set(evidence["blockers"]) == {
        "openrouter_key_not_configured",
        "opencode_go_key_not_configured",
    }
    assert evidence["secrets_emitted"] is False
