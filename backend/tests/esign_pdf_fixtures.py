"""Generated PDFs for the in-document signing tests.

Every fixture is built with reportlab/pypdf at test time so the suite does not
depend on binary files: an AcroForm with one of each input type plus a
signature widget, a flat agreement with printed signature lines, a form that
prints its label under the ruled line the way the firm's starter forms do,
and a blank document that has nothing to detect.
"""

from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def acroform_pdf() -> bytes:
    """Text, checkbox, choice and radio inputs plus a ``/Sig`` widget."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 700, "Client name:")
    pdf.acroForm.textfield(
        name="client_name",
        tooltip="Client name",
        x=150,
        y=690,
        width=200,
        height=20,
        fieldFlags="required",
    )
    pdf.drawString(72, 655, "I agree to the terms")
    pdf.acroForm.checkbox(name="agree", x=200, y=650, size=14, fieldFlags="required")
    pdf.drawString(72, 605, "State:")
    pdf.acroForm.choice(
        name="state",
        x=150,
        y=600,
        width=120,
        height=20,
        options=["TX", "OK"],
        value="TX",
    )
    pdf.drawString(72, 565, "Plan:")
    pdf.acroForm.radio(name="plan", value="A", x=150, y=560, size=12)
    pdf.acroForm.radio(name="plan", value="B", x=180, y=560, size=12)
    pdf.drawString(72, 210, "Client signature")
    pdf.showPage()
    pdf.save()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(buffer.getvalue())))
    page = writer.pages[0]
    signature = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Sig"),
            NameObject("/T"): TextStringObject("client_signature"),
            NameObject("/TU"): TextStringObject("Client signature"),
            NameObject("/Rect"): ArrayObject(
                [FloatObject(72), FloatObject(170), FloatObject(300), FloatObject(200)]
            ),
            NameObject("/F"): NumberObject(4),
            NameObject("/P"): page.indirect_reference,
        }
    )
    reference = writer._add_object(signature)
    page[NameObject("/Annots")].append(reference)
    writer._root_object["/AcroForm"]["/Fields"].append(reference)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def flat_agreement_pdf() -> bytes:
    """A printed agreement: underscore blanks and a label above a ruled line."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 700, "This agreement is signed by the parties below.")
    pdf.drawString(72, 300, "Name: ______________________")
    pdf.drawString(72, 200, "Client Signature: ______________________")
    pdf.drawString(360, 200, "Date: ____________")
    pdf.drawString(72, 150, "Attorney signature")
    pdf.line(72, 140, 300, 140)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def label_below_rule_pdf(label: str = "Client signature") -> bytes:
    """A ruled line with its 7.5pt label 9pt underneath, like the starter forms."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", 9.5)
    pdf.drawString(54, 700, "I confirm these answers are true and complete.")
    y = 528.5
    pdf.setLineWidth(0.8)
    pdf.line(54, y, 54 + 240, y)
    pdf.line(54 + 270, y, 54 + 400, y)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(54, y - 9, label)
    pdf.drawString(54 + 270, y - 9, "Date")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def blank_pdf(pages: int = 2, width: float = 612, height: float = 792) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
