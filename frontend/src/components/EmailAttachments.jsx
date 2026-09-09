import { useEffect, useRef, useState } from 'react'
import api, { getMatterDocuments, uploadMatterDocument } from '../api'
import GeneratedPdfPreview from './templates/GeneratedPdfPreview'
import MatterTemplatePicker from './templates/MatterTemplatePicker'

export default function EmailAttachments({ matterId, items, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const [templates, setTemplates] = useState(false)
  const [documents, setDocuments] = useState([])
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const urls = useRef([])
  useEffect(() => () => urls.current.forEach(url => URL.revokeObjectURL(url)), [])
  async function load() {
    setError(''); setOpen(true)
    try { const data = await getMatterDocuments(matterId); setDocuments(data.items || []) }
    catch { setError('Could not load matter documents.') }
  }
  async function review(id) {
    setBusy(true); setError('')
    try {
      const { data } = await api.get(`/matters/${matterId}/email-attachments/${id}/preview`)
      const bytes = Uint8Array.from(atob(data.content_base64), char => char.charCodeAt(0))
      const url = URL.createObjectURL(new Blob([bytes], { type: data.content_type }))
      urls.current.push(url); setPreview({ ...data, url })
    } catch (e) { setError(typeof e.response?.data?.detail === 'string' ? e.response.data.detail : 'Could not preview this document.') }
    finally { setBusy(false) }
  }
  async function upload(event) {
    const file = event.target.files?.[0]
    if (!file) return
    setBusy(true); setError('')
    try { const form = new FormData(); form.append('file', file); const doc = await uploadMatterDocument(matterId, form); await review(doc.id) }
    catch { setError('Could not upload this file to the matter.') }
    finally { setBusy(false); event.target.value = '' }
  }
  return <section aria-label="Email attachments" className="rounded-lg border border-brand-line p-3">
    <div className="flex flex-wrap gap-3"><button type="button" disabled={disabled || busy} onClick={load}>📎 Attach file</button><button type="button" disabled={disabled || busy} onClick={() => setTemplates(true)}>Attach template</button></div>
    <p className="mt-2 text-xs text-brand-muted">Review each file before attaching. Maximum 2 MiB total.</p>
    {items.map(item => <div key={item.document_id} className="mt-2 flex items-center justify-between gap-2 text-sm"><button type="button" disabled={disabled || busy} onClick={() => review(item.document_id)}>{item.filename}</button><button type="button" disabled={disabled} onClick={() => onChange(items.filter(value => value.document_id !== item.document_id))}>Remove {item.filename}</button></div>)}
    {open && <div className="mt-3 space-y-2"><label className="block text-sm">Upload into this matter<input type="file" disabled={disabled || busy} onChange={upload} /></label>{documents.map(doc => <button type="button" key={doc.id} disabled={disabled || busy} onClick={() => review(doc.id)} className="block text-sm underline">Preview {doc.filename}</button>)}<button type="button" onClick={() => setOpen(false)}>Close file picker</button></div>}
    {busy && <p role="status">Preparing document…</p>}{error && <p role="alert">{error}</p>}
    {templates && <MatterTemplatePicker matterId={matterId} onClose={() => setTemplates(false)} onSaved={result => { setTemplates(false); review(result.matter_document_id) }} />}
    {preview && <div className="mt-3 rounded border p-3"><strong>{preview.filename}</strong>{preview.content_type === 'application/pdf' ? <GeneratedPdfPreview key={preview.url} title={preview.filename} source={preview.url} /> : <a href={preview.url} download={preview.filename} className="my-2 block underline">Download document for review</a>}<button type="button" disabled={disabled || busy} onClick={() => { onChange([...items.filter(item => item.document_id !== preview.document_id), { document_id: preview.document_id, sha256: preview.sha256, filename: preview.filename }]); setPreview(null) }} className="mr-3 rounded border p-2">Reviewed — attach this version</button><button type="button" onClick={() => setPreview(null)}>Close preview</button></div>}
  </section>
}
