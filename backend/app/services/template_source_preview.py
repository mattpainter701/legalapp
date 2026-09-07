"""Bounded, tenant-scoped cache for read-only Word source previews.

The cache lives only for the worker lifetime, so a deployment changing fonts or
LibreOffice cannot reuse an old render. It holds at most 64 MiB / 32 documents.
Only one cache miss may convert per worker; concurrent misses fail promptly so
preview traffic cannot create an unbounded converter queue.
"""

from collections import OrderedDict
import hashlib

from app.services.docx_to_pdf import DocxToPdfError, docx_to_pdf_bytes


class SourcePreviewCache:
    def __init__(self, *, max_bytes=64 * 1024 * 1024, max_entries=32):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._bytes = 0
        self._busy = False

    async def render(
        self, source: bytes, *, tenant_id, template_id, **options
    ) -> bytes:
        key = (
            str(tenant_id),
            str(template_id),
            hashlib.sha256(source).hexdigest(),
            tuple(sorted(options.items())),
        )
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key]
        # No await between checking and claiming this worker's conversion slot.
        if self._busy:
            raise DocxToPdfError("Document preview is busy. Try again shortly.")
        self._busy = True
        try:
            output = await docx_to_pdf_bytes(source, **options)
            if len(output) <= self.max_bytes and self.max_entries > 0:
                while self._entries and (
                    self._bytes + len(output) > self.max_bytes
                    or len(self._entries) >= self.max_entries
                ):
                    _, removed = self._entries.popitem(last=False)
                    self._bytes -= len(removed)
                self._entries[key] = output
                self._bytes += len(output)
            return output
        finally:
            self._busy = False


source_preview_cache = SourcePreviewCache()
