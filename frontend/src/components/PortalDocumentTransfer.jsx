import { useEffect, useRef, useState } from 'react'
import {
  getClientPortalUploadLink,
  getClientPortalUploadPolicy,
  sendClientPortalMessage,
  uploadClientPortalDocument,
} from '../api'
import { FileText, Link2, Upload, X } from 'lucide-react'

const LIMIT = 10000
const DEFAULT_POLICY = { max_upload_bytes: 50 * 1024 * 1024, max_files_per_batch: LIMIT, allowed_extensions: [] }

export async function droppedFiles(dataTransfer) {
  const files = []
  async function visit(entry, prefix = '') {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject))
      files.push({ file, path: prefix + file.name })
      if (files.length > LIMIT) throw new Error('Select at most 10,000 files per batch.')
    } else if (entry.isDirectory) {
      const reader = entry.createReader()
      let children
      do {
        children = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
        for (const child of children) await visit(child, `${prefix}${entry.name}/`)
      } while (children.length)
    }
  }
  // Snapshot entries while the drop event still owns the browser's data store.
  const entries = Array.from(dataTransfer.items || []).map(item => item.webkitGetAsEntry?.()).filter(Boolean)
  const fallback = Array.from(dataTransfer.files || [])
  if (entries.length) for (const entry of entries) await visit(entry)
  else for (const file of fallback) files.push({ file, path: file.webkitRelativePath || file.name })
  return files
}

const FRIENDLY_EXTENSIONS = 'PDFs, Word documents, spreadsheets, images and scans'

function extensionText(policy) {
  if (!policy.allowed_extensions?.length) return FRIENDLY_EXTENSIONS
  return FRIENDLY_EXTENSIONS
}

export default function PortalDocumentTransfer({ matterName, onUploaded, onSessionError }) {
  const [entries, setEntries] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [uploadUrl, setUploadUrl] = useState(null)
  const [policy, setPolicy] = useState(DEFAULT_POLICY)
  const locked = useRef(false)

  useEffect(() => {
    let active = true
    getClientPortalUploadLink().then(result => { if (active) setUploadUrl(result.url) }).catch(() => {})
    getClientPortalUploadPolicy().then(result => { if (active && result) setPolicy({ ...DEFAULT_POLICY, ...result }) }).catch(() => {})
    return () => { active = false }
  }, [])

  const maxMb = Math.round((policy.max_upload_bytes || DEFAULT_POLICY.max_upload_bytes) / 1024 / 1024)
  const accept = (policy.allowed_extensions || []).map(extension => `.${extension}`).join(',') || undefined
  const actionable = entries.filter(entry => entry.status !== 'uploaded' && !entry.blocked)

  function select(files) {
    if (locked.current) return
    setError('')
    setNotice('')
    if (files.length > (policy.max_files_per_batch || LIMIT)) {
      setError(`Please send at most ${policy.max_files_per_batch || LIMIT} files at a time.`)
      return
    }
    setEntries(files.map(entry => {
      const tooBig = entry.file.size > (policy.max_upload_bytes || DEFAULT_POLICY.max_upload_bytes)
      return {
        ...entry,
        status: tooBig ? 'failed' : 'pending',
        error: tooBig ? `Too large — the limit is ${maxMb} MB per file.` : '',
        blocked: tooBig,
      }
    }))
  }

  async function upload() {
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError('')
    const next = entries.map(entry => ({ ...entry }))
    let sessionExpired = false
    for (const entry of next) {
      if (entry.status === 'uploaded' || entry.blocked) continue
      entry.status = 'uploading'
      entry.error = ''
      setEntries(next.map(row => ({ ...row })))
      try {
        await uploadClientPortalDocument(entry.file, description.trim() || undefined, undefined, entry.path)
        entry.status = 'uploaded'
      } catch (err) {
        entry.status = 'failed'
        entry.error = typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : 'Upload failed. Retry this file.'
        sessionExpired = onSessionError(err)
      }
      setEntries(next.map(row => ({ ...row })))
      if (sessionExpired) break
    }
    locked.current = false
    setBusy(false)
    if (!sessionExpired) await onUploaded()
  }

  async function submitLink(event) {
    event.preventDefault()
    if (locked.current) return
    setError('')
    let parsed
    try { parsed = new URL(url.trim()) } catch { setError('Enter a valid HTTPS sharing link.'); return }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) { setError('Enter an HTTPS sharing link without embedded credentials.'); return }
    locked.current = true
    setBusy(true)
    try {
      await sendClientPortalMessage({ subject: 'Files to import: shared folder', body: `Please review and import the files for this matter from this shared folder:\n${parsed.href}` })
      setUrl('')
      setNotice('Your legal team has the link. They will review the folder before anything is added to your matter.')
    } catch (err) {
      if (!onSessionError(err)) setError('Could not send the link. Please retry.')
    } finally { locked.current = false; setBusy(false) }
  }

  const remaining = actionable.length
  const uploadedCount = entries.filter(entry => entry.status === 'uploaded').length

  return <section className="space-y-4 font-sans" aria-label="Send documents to your legal team">
    <p className="text-sm text-brand-ink-2">
      Send documents to your legal team for {matterName || 'your matter'}. Choose a photo, a scan, or a file from
      your computer or phone. Everything you send is private to the firm.
    </p>
    <p className="text-xs text-brand-ink-2">
      {extensionText(policy)} · up to {maxMb} MB per file.
    </p>

    <label className="block text-sm text-brand-ink">
      What are you sending? (optional)
      <input
        value={description}
        maxLength={500}
        disabled={busy}
        onChange={event => setDescription(event.target.value)}
        placeholder="e.g. Photos of the letter I received"
        className="mt-1 block w-full border border-brand-line rounded-xl px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
      />
    </label>

    <div className="border-2 border-dashed border-brand-line rounded-xl p-5 space-y-3 bg-brand-bg-soft/40"
      onDragOver={event => event.preventDefault()}
      onDrop={async event => {
        event.preventDefault()
        if (locked.current) return
        try { select(await droppedFiles(event.dataTransfer)) } catch (err) { setError(err.message) }
      }}>
      <p className="text-sm text-brand-ink-2">Drag files here, or choose them below.</p>
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <label className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all cursor-pointer">
          <Upload size={15} /> Choose files
          <input className="sr-only" type="file" multiple accept={accept} disabled={busy} onChange={event => { select(Array.from(event.target.files || []).map(file => ({ file, path: file.name }))); event.target.value = '' }} />
        </label>
        <label className="inline-flex items-center justify-center gap-2 px-4 py-2.5 border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-xl hover:border-brand-ink transition-all cursor-pointer">
          <Upload size={15} /> Choose a folder (desktop)
          <input className="sr-only" type="file" multiple webkitdirectory="" directory="" accept={accept} disabled={busy} onChange={event => { select(Array.from(event.target.files || []).map(file => ({ file, path: file.webkitRelativePath || file.name }))); event.target.value = '' }} />
        </label>
      </div>
    </div>

    {!!entries.length && <>
      <p role="status" className="text-sm text-brand-ink-2">{uploadedCount} of {entries.length} files sent</p>
      <ul className="max-h-56 overflow-auto text-sm divide-y divide-brand-line border border-brand-line rounded-xl">
        {entries.map((entry, index) => (
          <li key={index} className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="flex items-center gap-2 min-w-0">
              <FileText size={15} className="text-brand-ink-2 shrink-0" />
              <span className="truncate text-brand-ink">{entry.path}</span>
            </span>
            <span className={`shrink-0 text-xs ${entry.status === 'failed' ? 'text-brand-rose' : entry.status === 'uploaded' ? 'text-brand-green' : 'text-brand-ink-2'}`}>
              {entry.status === 'uploaded' ? 'Sent' : entry.status === 'uploading' ? 'Sending…' : entry.error || 'Ready'}
            </span>
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50"
        disabled={busy || remaining === 0}
        onClick={upload}
      >
        {busy ? 'Sending…' : remaining === 0 ? 'All files sent' : `Send ${remaining} file${remaining === 1 ? '' : 's'}`}
      </button>
      <p className="text-xs text-brand-ink-2">Keep this page open until every file is sent. A ZIP is stored as a bundle; your legal team can unpack it.</p>
    </>}

    {uploadUrl && (
      <p>
        <a href={uploadUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-sm text-brand-accent hover:underline">
          <Link2 size={15} /> Open the upload folder shared by your legal team
        </a>
      </p>
    )}

    <form onSubmit={submitLink} className="space-y-2 border-t border-brand-line pt-4">
      <label className="block text-sm text-brand-ink">
        Or paste a link to a shared folder
        <input
          type="url"
          required
          maxLength={2000}
          value={url}
          onChange={event => setUrl(event.target.value)}
          className="mt-1 block w-full border border-brand-line rounded-xl px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
          placeholder="https://…"
          disabled={busy}
        />
      </label>
      <p className="text-xs text-brand-ink-2">Give your legal team access using your storage provider&apos;s sharing controls. They will review the link before importing anything.</p>
      <button className="inline-flex items-center gap-2 px-4 py-2.5 border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-xl hover:border-brand-ink transition-all disabled:opacity-50" disabled={busy || !url.trim()}>
        <Link2 size={15} /> Send link to my legal team
      </button>
    </form>

    {error && <p role="alert" className="flex items-start gap-2 text-sm text-brand-rose"><X size={15} className="mt-0.5 shrink-0" /> {error}</p>}
    {notice && <p role="status" className="text-sm text-brand-green">{notice}</p>}
  </section>
}
