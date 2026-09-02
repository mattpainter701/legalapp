# LawHand Search Node extraction and OCR

This package is the default-off extraction boundary for the Firm Memory scale
pipeline. It does not run in the LawHand backend or SMB agent API process. It
defines typed `ManifestQueue` and `SearchSink` ports, supervises a disposable
parser child for every document, and runs OCR in a separate off-hours pool.

The package intentionally contains no OpenSearch client, crawler reconciliation,
portal surface, per-user ACL filtering, or embeddings. Those systems consume or
implement the contracts in `search_node.contracts`.

The contracts retain the FM-04 stable source/file/content identity and lease
generation needed for a future queue adapter, plus the deterministic chunks and
metadata needed for a thin FM-03 `LocalSearchEngine` sink adapter. Neither
parallel branch is imported here.

## Local checks

```powershell
cd search-node
$env:PYTHONPATH = "src"
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
```

The parsers used by tests are inert Python/PyPDF parsers. Production legacy
Office, Outlook MSG, and OpenDocument coverage requires a locally mounted Tika
application JAR. OCR requires local `pdftoppm`, Tesseract, and the configured
language packs. No worker downloads tools, models, or language data at runtime.

See [the operator guide](../docs/search-node-extraction-ocr.md) before enabling
either pool.
