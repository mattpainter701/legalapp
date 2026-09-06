import { useEffect, useState } from 'react'
import { getMatterDocuments, getMatterDocumentDownloadUrl, proposeTemplateFact, acceptTemplateFact } from '../../api'

export default function TemplateFactReview({ matterId, fields, onAccepted }) {
  const [documents, setDocuments] = useState([])
  const [documentId, setDocumentId] = useState('')
  const [fieldId, setFieldId] = useState('')
  const [proposal, setProposal] = useState(null)
  const [value, setValue] = useState('')
  const [replace, setReplace] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const eligible = fields.filter(field => field.binding?.startsWith('custom.matter.'))
  useEffect(() => {
    let current = true
    setProposal(null)
    setDocuments([])
    setDocumentId('')
    if (matterId && eligible.length) getMatterDocuments(matterId).then(result => {
      if (current) setDocuments((result.items || result.documents || result || []).filter(doc => /\.(pdf|docx|txt)$/i.test(doc.filename)))
    }).catch(() => { if (current) setMessage('Matter source documents could not be loaded.') })
    return () => { current = false }
  }, [matterId, eligible.length])
  if (!matterId || !eligible.length) return null
  const reset = () => { setProposal(null); setReplace(false); setMessage('') }
  const find = async () => {
    setBusy(true); reset()
    try {
      const result = await proposeTemplateFact(matterId, documentId, fieldId)
      setProposal(result)
      const candidate = result.status === 'suggested' ? result.candidates.find(item => item.value != null)?.value : ''
      setValue(candidate == null ? '' : String(candidate))
    } catch (error) { setMessage(error?.response?.data?.detail || 'The source could not be read.') }
    finally { setBusy(false) }
  }
  const accept = async () => {
    setBusy(true)
    try {
      await acceptTemplateFact(matterId, documentId, fieldId, { proposal_token: proposal.proposal_token, value, replace_existing: replace })
      setProposal(null); setMessage('Reviewed matter detail saved. Run Smart Fill to reuse it.'); onAccepted?.()
    } catch (error) { setMessage(error?.response?.data?.detail || 'Review could not be saved.') }
    finally { setBusy(false) }
  }
  return <details className="rounded border border-brand-line p-3">
    <summary className="cursor-pointer font-semibold text-sm">Review a detail from a matter document</summary>
    <p className="my-2 text-xs text-brand-muted">Finds exact “Label: value” lines in PDF, Word and text files. Scans, tables and unlabeled details may need manual entry. Nothing is saved until you accept it.</p>
    <label className="block text-xs">Source document<select aria-label="Fact source document" value={documentId} disabled={busy} onChange={event => { setDocumentId(event.target.value); reset() }} className="block w-full border rounded p-2 text-brand-ink bg-brand-bg"><option value="">Choose a source</option>{documents.map(doc => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}</select></label>
    <label className="block text-xs mt-2">Matter detail<select aria-label="Fact to review" value={fieldId} disabled={busy} onChange={event => { setFieldId(event.target.value); reset() }} className="block w-full border rounded p-2 text-brand-ink bg-brand-bg"><option value="">Choose a detail</option>{eligible.map(field => <option key={field.name} value={field.binding.slice('custom.matter.'.length)}>{field.label || field.name}</option>)}</select></label>
    <button type="button" onClick={find} disabled={busy || !fieldId || !documentId} className="mt-2 border rounded p-2 text-sm">Read source for review</button>
    {proposal && <div className="mt-3 space-y-2 text-sm">
      <p>{proposal.status === 'conflicting_sources' ? 'Conflicting values found. Resolve them against the original document.' : proposal.status === 'missing' ? 'No supported value found. Read the original before entering a value.' : 'Suggested from the source; verify before accepting.'}</p>
      <a className="underline" href={getMatterDocumentDownloadUrl(matterId, documentId)} target="_blank" rel="noreferrer">Open {proposal.source_filename}</a>
      <ul>{proposal.candidates.map((candidate, index) => <li key={index}>Extracted text line {candidate.line}: {candidate.value == null ? 'Unsupported value - enter after review' : String(candidate.value)}</li>)}</ul>
      <p>Current matter value: {proposal.current_value == null ? 'Missing' : String(proposal.current_value)}</p>
      <label className="block">Reviewed value<input aria-label="Reviewed fact value" value={value} onChange={event => setValue(event.target.value)} className="block border rounded p-2 w-full text-brand-ink bg-brand-bg" /></label>
      {proposal.current_value != null && <label className="flex gap-2"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />Replace the existing matter value with my reviewed value</label>}
      <button type="button" disabled={busy || !value.trim()} onClick={accept} className="border rounded p-2 font-semibold">Accept reviewed matter detail</button>
    </div>}
    {message && <p role="status" className="mt-2 text-sm">{message}</p>}
  </details>
}
