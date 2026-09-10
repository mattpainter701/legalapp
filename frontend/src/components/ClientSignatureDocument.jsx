import { useEffect, useState } from 'react'
import api, { downloadClientPortalDocumentUrl } from '../api'
import GeneratedPdfPreview from './templates/GeneratedPdfPreview'

export default function ClientSignatureDocument({ request, reviewed, onReviewed }) {
  const [source, setSource] = useState(null)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setSource(null); setError(false)
    api.get(`/portal/client/documents/${request.document_id}/download`, { responseType: 'blob' })
      .then(({ data }) => { if (active) setSource(data) })
      .catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [request.document_id, attempt])
  return <div className="space-y-3 mb-4">
    {source ? <GeneratedPdfPreview source={source} title={request.document_name || 'Document to sign'} /> : error ? <p role="alert">The document could not be loaded. <button type="button" className="underline" onClick={() => setAttempt(value => value + 1)}>Retry document</button></p> : <p role="status">Loading document to review…</p>}
    <a className="block underline" href={downloadClientPortalDocumentUrl(request.document_id)}>Download document to review</a>
    <label className="flex items-start gap-2 text-sm"><input type="checkbox" disabled={!source} checked={reviewed} onChange={event => onReviewed(event.target.checked)} /> I have reviewed every page of this document.</label>
  </div>
}
