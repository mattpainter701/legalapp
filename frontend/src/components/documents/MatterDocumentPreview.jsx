import { useEffect, useState } from 'react'
import { getMatterDocumentDownloadUrl, getMatterDocumentSigningSource } from '../../api'
import GeneratedPdfPreview from '../templates/GeneratedPdfPreview'

export default function MatterDocumentPreview({ matterId, document }) {
  const [source, setSource] = useState(null)
  const [error, setError] = useState('')
  const pdf = document.content_type === 'application/pdf' || document.filename?.toLowerCase().endsWith('.pdf')
  useEffect(() => {
    setSource(null)
    setError('')
    if (!pdf) return undefined
    let cancelled = false
    getMatterDocumentSigningSource(matterId, document.id)
      .then(blob => { if (!cancelled) setSource(blob) })
      .catch(() => { if (!cancelled) setError('Could not load the PDF preview. Download it to review, or close and reopen the preview to retry.') })
    return () => { cancelled = true }
  }, [matterId, document.id, pdf])
  return <div className="mt-3 space-y-3">
    {pdf && !source && !error && <p role="status">Loading PDF preview…</p>}
    {error && <p role="alert">{error}</p>}
    {source && <GeneratedPdfPreview source={source} title={document.filename} />}
    <a href={getMatterDocumentDownloadUrl(matterId, document.id)} className="inline-block text-sm underline">Download {document.filename}</a>
  </div>
}
