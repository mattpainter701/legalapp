"""Read-only, unsaved Word previews using the same bounded converter as Studio."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.routers.document_templates import _read_template_sample, settings
from app.services.docx_to_pdf import DocxToPdfError
from app.services.template_source_preview import source_preview_cache
from app.services.access_control import require_capability


router = APIRouter(prefix="/api/templates/intake", tags=["templates"])


@router.post("/preview-render")
async def preview_word_upload(
    file: UploadFile = File(...),
    current_user=Depends(require_capability("manage_documents")),
):
    sample = await _read_template_sample(file)
    if not sample.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=422, detail="Document preview requires a DOCX file."
        )
    if not settings.DOCX_PDF_CONVERSION_ENABLED:
        raise HTTPException(
            status_code=503, detail="Word-to-PDF conversion is unavailable."
        )
    try:
        output = await source_preview_cache.render(
            sample.content,
            tenant_id=current_user.tenant_id,
            template_id="unsaved-upload",
            executable=settings.DOCX_PDF_CONVERTER_PATH,
            timeout_seconds=settings.DOCX_PDF_CONVERSION_TIMEOUT_SECONDS,
            max_output_bytes=settings.DOCX_PDF_CONVERSION_MAX_OUTPUT_BYTES,
            max_pages=settings.DOCX_PDF_CONVERSION_MAX_PAGES,
        )
    except DocxToPdfError as exc:
        raise HTTPException(
            status_code=503,
            detail="The document preview could not be prepared. Try again or use Fields to continue.",
        ) from exc
    return Response(
        content=output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="upload-preview.pdf"',
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
