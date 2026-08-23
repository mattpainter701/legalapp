# Product & UX review — paralegal's-seat pass

Reviewed on branch `claude/product-ux-code-review-tf7ake`, focused on the
document pipeline: templates, generation, upload, matter files, revisions, and
outbound delivery.

The frame is a paralegal under load. Their day is not judgment calls — it is
volume. Generate the packet, fill the caption, get it filed, get it served, get
it into the file, and do it again for the next matter before 5 p.m. They are
measured on throughput and on never sending the wrong version to the wrong
person.

That persona breaks a product in different places than an attorney does. The
attorney findings are in `PRODUCT_UX_REVIEW.md` and still apply. These are new.

---

## P0 — Silently destroys work or invites a mis-filing

### 1. Dropped files vanish with no message at all

`frontend/src/components/FileUpload.jsx:7-11,93-98`

```js
const ACCEPTED_TYPES = {
  'application/pdf': ['.pdf'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'text/plain': ['.txt'],
}
...
const { getRootProps, getInputProps, isDragActive } = useDropzone({
  onDrop, accept: ACCEPTED_TYPES, maxSize: 50 * 1024 * 1024,
})
```

There is **no `onDropRejected` handler and no `fileRejections` rendering**.
react-dropzone rejects silently by default, so a file that fails either check
produces no error, no toast, no row in the upload list. Nothing happens.

What gets silently swallowed is exactly what arrives in a law office:

- `.doc` — legacy Word, still standard at many courts and older firms
- `.jpg` / `.png` / `.tiff` — scanned exhibits, photos of documents
- `.msg` / `.eml` — email produced as an exhibit
- `.xlsx` — damages calculations, billing records, medical expense schedules
- `.rtf`, `.wpd` — older court forms
- Anything over 50 MB — a scanned deposition transcript or a medical-records
  production clears that routinely

The paralegal drags the file. Nothing. Drags again. Nothing. Concludes the site
is broken, or worse, believes the upload worked and moves on.

Three separate fixes, all small:
- Render `fileRejections` with the actual reason ("PDF, DOCX, and TXT only" /
  "files must be under 50 MB").
- Widen `ACCEPTED_TYPES` to what a firm actually handles.
- Raise or make the size cap configurable, and say the number in the dropzone.

### 2. Every generated document has the same filename

`backend/app/routers/document_templates.py:254-258,1972`

```python
def _safe_generated_filename(title: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", (title or "generated-document"))
    ...
output_filename = _safe_generated_filename(template.title, ...)
```

The output name is the **template title and nothing else**. Generate "Motion to
Compel" for Smith, then Jones, then Garcia, and all three files are
`Motion to Compel.pdf`. Download them to one folder and the operating system
renames them `Motion to Compel (1).pdf` and `Motion to Compel (2).pdf`.

The paralegal now holds three identical-looking motions for three different
clients, distinguishable only by opening each one. This is how the wrong motion
gets attached to the wrong email. It is the highest-consequence, lowest-effort
fix on this list.

The render call already has `matter_id` in scope. Build the stem from matter
name or case number plus template title plus date:
`Smith-v-Jones_Motion-to-Compel_2026-08-23.pdf`.

### 3. Smart Fill silently overwrites hand-typed corrections

`frontend/src/pages/TemplatesPage.jsx:1020`

```js
setVariables((prev) => ({ ...prev, ...discovered }))
```

`discovered` spreads last, so machine values win over anything already in the
form. A paralegal who notices the client's address changed, types the correct
one, then hits Smart Fill to populate the remaining twenty fields has their
correction replaced by the stale matter value. No warning, no undo, no
indication it happened.

Prefer existing user input, or surface a conflict per field and let them choose.

### 4. Smart Fill marks nothing as machine-filled

Same flow. The banner reads *"Smart-fill values loaded. Review each field before
saving."* — but nothing distinguishes a field the paralegal typed from one the
system guessed. On a 25-field PDF that instruction means re-reading all 25 with
no idea which three came from the matter record and which came from a model.

The product already has the right pattern elsewhere. `README.md` documents a
rigorous chat citation contract — `cited` / `verify` / `model`, with source IDs
and character offsets, so a reviewing attorney knows the provenance of every
claim. Document generation, which produces the thing that actually gets filed
and served, has none of it.

Mark smart-filled fields visually, show the source (which matter field it came
from), and let the paralegal accept per field. That converts "review 25 fields"
into "confirm 3."

### 5. Deleting a matter document destroys the file permanently

`backend/app/routers/matter_documents.py:461-473`

```python
await _delete_cloud_backing_if_needed(doc, db)
if doc.storage_path and os.path.exists(doc.storage_path):
    os.remove(doc.storage_path)
    ...
await db.delete(doc)
```

Cloud copy removed, file removed from disk, row hard-deleted. There is a confirm
dialog and a good guard against deleting anything in a revision lineage
(`:449`), but an ordinary document — a signed retainer, a filed pleading, a
produced exhibit — is unrecoverable after one misclick on a dense list.

For a legal product this is more than a UX problem. Firms carry records-retention
obligations, and destroying a produced document is a spoliation exposure. A
paralegal cleaning up duplicates at 6 p.m. should not be able to do that.

Soft-delete with `deleted_at`, a Trash view, and a retention window before the
bytes actually go.

### 6. Uploads run one at a time and leak a timer per file

`frontend/src/components/FileUpload.jsx:47-90`

```js
for (const file of acceptedFiles) {
  ...
  const doc = await uploadDocument(file)      // strictly serial
  ...
  const poll = setInterval(async () => {
    const docs = await getDocuments()          // full list, every 3s, per file
    ...
  }, 3000)
}
```

Two problems in one loop.

**Serial uploads.** `await` inside `for...of` means a 30-file production uploads
one file at a time, each waiting on the last. A bounded pool of 3–5 concurrent
uploads would cut that several-fold.

**Leaked intervals.** Each file spawns its own `setInterval` and there is **no
`useEffect` cleanup anywhere in the component** — `clearInterval` only runs from
inside the callback, on success or after 30 attempts. Close the modal or
navigate away mid-upload and every timer keeps running for up to 90 seconds,
each one fetching the *entire* document list every 3 seconds. Twenty files means
roughly 7 full-list requests per second against a component that no longer
exists.

Add a cleanup effect that clears all pending intervals on unmount, and poll the
single document rather than re-fetching the whole list.

---

## P1 — Turns a batch job into a hundred single jobs

### 7. There are no bulk operations anywhere in the product

```
grep -rn "bulk|batch" frontend/src/pages frontend/src/components  → 0 hits
```

Not one. This is the defining gap for this persona, because a paralegal's work
is inherently batched:

- A discovery packet is 8 documents for **one** matter — currently 8 trips
  through the Generate modal, re-picking the matter each time.
- A client mailing is **one** document across 30 recipients — not expressible
  at all.
- Filing a production means uploading 200 exhibits — one dropzone, serial.
- Closing out a case means moving or tagging dozens of documents — one at a time.

There are no checkboxes on any document list, so there is no multi-select to
build on. The nearest thing to batch work in the product is the intake draft tab
strip, which handles several calls at once and is the right instinct applied to
the wrong surface.

At minimum: multi-select on matter documents, and "generate these N templates
for this matter" in one pass.

### 8. The product does the prep and abandons the push

`frontend/src/components/ComposeEmailModal.jsx` (123 lines total)

The composer is **To, Subject, Message**. No attachments. No CC. No BCC.

So the actual workflow is: generate the document in the product → download it →
open Outlook → find the recipient again → attach → send → come back and log the
communication by hand. The "pushing" half of "document prep and pushing" happens
entirely outside the product, which means the matter file is only as complete as
the paralegal's discipline about returning to it.

No CC/BCC is its own problem — legal correspondence is nearly always copied to
the supervising attorney and the client.

Attaching a matter document to an outbound email, from the matter, with CC/BCC,
and auto-logging it to the communications timeline is the single feature that
would most change this persona's day.

### 9. Matter documents have no search, no filter, no pagination

`frontend/src/components/MatterDocumentsTab.jsx` (864 lines)

```
grep -n "limit|offset|page"   → 0 hits
grep -n "search|filter"       → 0 hits (only Array.filter on delete)
```

Every document on the matter is fetched and rendered at once, with no way to
narrow. On a litigation matter with 800 produced documents that is an
unsearchable wall of rows and a slow render, and finding "the November 3
production letter" means scrolling.

This is the list the paralegal lives in all day. It needs a filename search box,
a type/date filter, and either pagination or virtualization.

### 10. Client list silently stops at 100

`frontend/src/pages/ClientsPage.jsx:161`

```js
const params = { limit: 100, sort }
```

No "load more", no pagination control, no total count, no indication the list is
truncated. Client 101 does not exist as far as the UI is concerned. A firm
crosses 100 clients early, and the failure is silent — nobody gets an error,
they just cannot find the client and assume it was never entered.

### 11. Revision history is cut off at five with no way to see the rest

`frontend/src/pages/DocumentRevisionPage.jsx:327`

```js
{priorRevisions.slice(0, 5).map((item) => (
```

Hard slice, no "show all". Past the fifth revision, earlier versions are
unreachable in the UI.

"Which version actually went out?" is a question a paralegal has to answer under
pressure — before a filing deadline, or when opposing counsel says they received
something different. On a document that has been through seven rounds of
partner edits, the product cannot answer it.

---

## P2 — Friction and honesty

### 12. Upload progress is a word, not a bar

`FileUpload.jsx:13-27` renders a spinner and a status string
(`uploading` → `processing` → `indexed`). A 45 MB scanned transcript shows
"uploading" with no percentage for two minutes. There is no way to distinguish
slow from stuck, so the paralegal cancels and retries a large file that was
almost done.

`uploadDocument` can carry an axios `onUploadProgress`; wiring it to a real bar
is contained work.

### 13. Template upload copy promises more than the picker accepts

`TemplatesPage.jsx:569` tells the user:

> "We automatically read Word files, ordinary PDFs, fillable PDFs, and image-only
> scans while preserving the original design."

"Image-only scans" here means a scanned *PDF*, but it reads as "send us your
scans," and the file picker rejects `.jpg`, `.png`, and `.tiff` — silently, per
finding #1. Say "scanned PDFs" and the promise matches the behavior.

---

## Suggested order

1. **#1 dropped files vanish** — smallest fix, largest daily aggravation, and it
   currently reads as "the product is broken."
2. **#2 duplicate filenames** — two lines, and it removes a wrong-client-served
   risk.
3. **#3 / #4 Smart Fill** — stop clobbering typed values, then mark provenance.
   Both are needed before a paralegal can trust generation at volume.
4. **#5 soft-delete** — schema change, so do it before customer documents exist.
5. **#6 upload concurrency and interval cleanup** — correctness and a real leak.
6. **#8 email attachments with CC/BCC** — the largest single change to this
   persona's day, and it closes the loop the product currently opens.
7. **#9 / #10 / #11 pagination and search** — everything is fine at demo scale
   and breaks quietly at real scale.
8. **#7 bulk operations** — the largest effort; multi-select on matter documents
   is the foothold.
