import { useEffect, useState } from 'react'
import { getMatterPortalUploadLink, setMatterPortalUploadLink } from '../api'

export default function MatterTransferSettings({ matterId }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    let active = true
    setLoaded(false)
    getMatterPortalUploadLink(matterId).then(result => {
      if (active) { setUrl(result.url || ''); setLoaded(true) }
    }).catch(() => { if (active) setMessage('Could not load the portal upload link.') })
    return () => { active = false }
  }, [matterId])
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setMessage('')
    try {
      await setMatterPortalUploadLink(matterId, url.trim() || null)
      setMessage(url.trim() ? 'Upload link published in the client portal.' : 'Upload link removed from the client portal.')
    } catch (err) {
      setMessage(typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : 'Could not save the upload link.')
    } finally { setBusy(false) }
  }
  return <form onSubmit={save} className="space-y-3">
    <p>Publish a client-safe upload or file-request link. Configure access in your storage provider before sharing it; only this explicit link appears in the portal.</p>
    <label className="block">Client upload-folder link<input type="url" maxLength={2000} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://…" className="block w-full border rounded-lg p-2" disabled={!loaded || busy} /></label>
    <button className="border rounded-lg px-4 py-2" disabled={!loaded || busy}>{busy ? 'Saving…' : 'Save portal upload link'}</button>
    <p className="text-xs">Clear the field and save to remove it. For source-folder binding and sync, use this matter’s File Shares tab. Client-submitted source links arrive in Correspondence as portal messages.</p>
    {message && <p role="status">{message}</p>}
  </form>
}
