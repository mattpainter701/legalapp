import { useEffect, useRef, useState } from 'react'
import { getClientPortalUploadLink, sendClientPortalMessage, uploadClientPortalDocument } from '../api'

const LIMIT = 10000

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

export default function PortalDocumentTransfer({ matterName, onUploaded, onSessionError }) {
  const [entries, setEntries] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [uploadUrl, setUploadUrl] = useState(null)
  const locked = useRef(false)

  useEffect(() => {
    let active = true
    getClientPortalUploadLink().then(result => { if (active) setUploadUrl(result.url) }).catch(() => {})
    return () => { active = false }
  }, [])

  function select(files) {
    if (locked.current) return
    setError('')
    setNotice('')
    if (files.length > LIMIT) { setError('Select at most 10,000 files per batch.'); return }
    setEntries(files.map(entry => ({ ...entry, status: 'pending', error: '' })))
  }

  async function upload() {
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError('')
    const next = entries.map(entry => ({ ...entry }))
    let sessionExpired = false
    for (const entry of next) {
      if (entry.status === 'uploaded') continue
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
      setNotice('Link sent to your legal team for review. Files have not been imported yet.')
    } catch (err) {
      if (!onSessionError(err)) setError('Could not send the link. Please retry.')
    } finally { locked.current = false; setBusy(false) }
  }

  return <section className="space-y-3" aria-label="Transfer matter documents">
    <p>Send files for {matterName || 'this matter'} only. Choose this client’s folder from your computer or USB drive. Your legal team can import multiple clients through New Matter.</p>
    <label className="block">Description (optional)<input value={description} maxLength={500} disabled={busy} onChange={event => setDescription(event.target.value)} className="block w-full border rounded-lg p-2" /></label>
    <div className="border-2 border-dashed border-brand-line rounded-xl p-5 space-y-3"
      onDragOver={event => event.preventDefault()}
      onDrop={async event => {
        event.preventDefault()
        if (locked.current) return
        try { select(await droppedFiles(event.dataTransfer)) } catch (err) { setError(err.message) }
      }}>
      <p>Drop files or a folder here, or select them below.</p>
      <label className="block">Choose files<input className="block" type="file" multiple disabled={busy} onChange={event => { select(Array.from(event.target.files || []).map(file => ({ file, path: file.name }))); event.target.value = '' }} /></label>
      <label className="block">Choose folder<input className="block" type="file" multiple webkitdirectory="" directory="" disabled={busy} onChange={event => { select(Array.from(event.target.files || []).map(file => ({ file, path: file.webkitRelativePath || file.name }))); event.target.value = '' }} /></label>
    </div>
    {!!entries.length && <>
      <p role="status">{entries.filter(entry => entry.status === 'uploaded').length} of {entries.length} files uploaded</p>
      <ul className="max-h-56 overflow-auto text-sm">{entries.map((entry, index) => <li key={index}>{entry.path} — {entry.status}{entry.error && `: ${entry.error}`}</li>)}</ul>
      <button type="button" className="border rounded-lg px-4 py-2" disabled={busy || entries.every(entry => entry.status === 'uploaded')} onClick={upload}>{busy ? 'Uploading…' : 'Upload remaining files / retry failures'}</button>
      <p className="text-xs">Keep this page open until every file is accounted for. ZIPs are stored as bundles; ask your legal team to unpack them. Email originals are preserved for review.</p>
    </>}
    {uploadUrl && <p><a href={uploadUrl} target="_blank" rel="noopener noreferrer" className="underline">Open the upload folder shared by your legal team</a></p>}
    <form onSubmit={submitLink} className="space-y-2">
      <label className="block">Or send a cloud/fileshare link<input type="url" required maxLength={2000} value={url} onChange={event => setUrl(event.target.value)} className="block w-full border rounded-lg p-2" placeholder="https://…" disabled={busy} /></label>
      <p className="text-xs">Give your legal team access using your storage provider’s sharing controls. They will review the link before importing.</p>
      <button className="border rounded-lg px-4 py-2" disabled={busy || !url.trim()}>Send link for import</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
