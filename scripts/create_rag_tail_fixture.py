"""Create a synthetic attachment with independent beginning and tail controls."""

import hashlib
import json
from pathlib import Path


out = Path(__file__).resolve().parents[1] / "output" / "rag-validation"
out.mkdir(parents=True, exist_ok=True)
prefix = (
    "Synthetic long-document retrieval control. Fictional; no client information.\n"
    "Record family: TAIL-CONTROL-20260907.\n"
    "The opening checkpoint label is SCARLET-HERON-3148.\n\n"
)
filler = "\n".join(
    f"Archive entry {index:03d}: Routine fictional packing record; no checkpoint changes."
    for index in range(1, 121)
)
tail = (
    "\n\nFinal signed addendum:\n"
    "The final checkpoint label is TEAL-LYNX-8625.\n"
    "The final reviewer is Anika Frost.\n"
)
content = prefix + filler + tail
path = out / "CSA_RAG_20260907_Long_Record.txt"
path.write_text(content, encoding="utf-8")
manifest = {
    "filename": path.name,
    "characters": len(content),
    "tail_offset": content.index("Final signed addendum"),
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "opening_label": "SCARLET-HERON-3148",
    "final_label": "TEAL-LYNX-8625",
    "final_reviewer": "Anika Frost",
}
(out / "tail-answer-manifest.json").write_text(
    json.dumps(manifest, indent=2), encoding="utf-8"
)
print(json.dumps(manifest, indent=2))
