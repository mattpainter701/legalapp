"""The printed pack must carry the same fields as the templates it comes from."""

import importlib.util
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services import intake_starter_pack as pack

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_intake_starter_pdfs.py"
)


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("intake_starter_pdfs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("document", pack.documents(), ids=lambda d: d.key)
def test_a_printed_template_is_fillable_on_every_declared_field(
    generator, tmp_path, document
):
    """A field missing from the PDF is data the client cannot give back."""

    path = generator.render_document(document, tmp_path / f"{document.key}.pdf")
    fields = PdfReader(path).get_fields() or {}

    assert set(fields) == {field.name for field in document.fields}


@pytest.mark.parametrize("practice", pack.practices(), ids=lambda p: p.slug)
def test_a_printed_questionnaire_uses_the_question_keys(generator, tmp_path, practice):
    """Answers come back on the keys the intake packet already stores them under."""

    path = generator.render_questionnaire(practice, tmp_path / f"{practice.slug}.pdf")
    fields = PdfReader(path).get_fields() or {}

    assert set(fields) == {
        question["key"] for question in pack.questionnaire(practice.slug)
    }


def test_conditional_fee_sections_are_all_printed(generator, tmp_path):
    """A form has no renderer to choose one fee arrangement, so it shows each."""

    path = generator.render_document(pack.FEE_AGREEMENT, tmp_path / "fee.pdf")
    # Words are drawn one at a time, so extraction returns them one per line.
    text = " ".join(
        "".join(page.extract_text() for page in PdfReader(path).pages).split()
    )

    assert "Hourly fees." in text
    assert "Flat fee." in text
    assert "Contingency fee." in text
    assert "{{" not in text and "#if" not in text


@pytest.fixture(scope="module")
def sampler():
    path = SCRIPT.parent / "render_starter_sample.py"
    spec = importlib.util.spec_from_file_location("render_starter_sample", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_word_sample_reads_as_a_completed_agreement(sampler, tmp_path):
    """A reviewer reads wording, so no placeholder may survive into the sample."""

    from docx import Document

    document = sampler.pack.HOURLY_FEE_AGREEMENT_ND
    path = sampler.to_docx(document, sampler._values(document), tmp_path / "s.docx")
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)

    assert "{{" not in text and "#if" not in text
    assert "$3,500.00" in text and "Cass County, North Dakota" in text
    assert "Section 9." in text


def test_sample_values_fill_every_field_the_template_declares(sampler):
    """A field with neither a sample value nor a default would render blank."""

    document = sampler.pack.HOURLY_FEE_AGREEMENT_ND

    assert not {field.name for field in document.fields} - set(
        sampler._values(document)
    )
