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


def _widgets(path):
    """Every widget's page, field name, and rectangle, for geometry checks."""

    reader = PdfReader(path)
    widgets = []
    for page_number, page in enumerate(reader.pages):
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            name, rect = widget.get("/T"), widget.get("/Rect")
            if name is None or rect is None:
                continue
            widgets.append((page_number, str(name), [float(v) for v in rect]))
    return reader, widgets


def _assert_no_overlap(path):
    reader, widgets = _widgets(path)
    by_page: dict[int, list] = {}
    for page_number, name, rect in widgets:
        by_page.setdefault(page_number, []).append((name, rect))
    for page_number, items in by_page.items():
        media = reader.pages[page_number].mediabox
        for name, rect in items:
            assert media.bottom <= rect[1] and rect[3] <= media.top
            assert media.left <= rect[0] and rect[2] <= media.right
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                left = max(items[i][1][0], items[j][1][0])
                right = min(items[i][1][2], items[j][1][2])
                bottom = max(items[i][1][1], items[j][1][1])
                top = min(items[i][1][3], items[j][1][3])
                overlap = max(0.0, right - left) * max(0.0, top - bottom)
                # Fields on consecutive lines used to overlap by a hair, which
                # reads as a double rule; nothing may cross by more than that.
                assert overlap <= 1.0, (page_number, items[i][0], items[j][0])


@pytest.mark.parametrize("document", pack.documents(), ids=lambda d: d.key)
def test_a_printed_template_has_no_overlapping_fields(generator, tmp_path, document):
    """Boxes that touch or cross look broken on paper; none may overlap."""

    path = generator.render_document(document, tmp_path / f"{document.key}.pdf")

    _assert_no_overlap(path)


@pytest.mark.parametrize("practice", pack.practices(), ids=lambda p: p.slug)
def test_a_printed_questionnaire_has_no_overlapping_fields(
    generator, tmp_path, practice
):
    path = generator.render_questionnaire(practice, tmp_path / f"{practice.slug}.pdf")

    _assert_no_overlap(path)


def test_a_declared_default_is_prefilled_into_the_printed_field(generator, tmp_path):
    """A jurisdiction-settled term must show, not sit in an empty box."""

    path = generator.render_document(
        pack.HOURLY_FEE_AGREEMENT_ND, tmp_path / "fee-nd.pdf"
    )
    fields = PdfReader(path).get_fields() or {}
    default = next(
        field.default
        for field in pack.HOURLY_FEE_AGREEMENT_ND.fields
        if field.name == "billing_increment"
    )

    assert fields["billing_increment"]["/V"] == default


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
