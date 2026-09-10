import { useState } from 'react'
import { FileCheck2 } from 'lucide-react'
import { installIntakeStarterDocuments } from '../../api'

// Step one of matter initiation sends the same two documents to every client.
// They install as unapproved drafts: fee, trust-account and contingency terms
// are jurisdiction-regulated, so an attorney approves them here before a client
// ever receives one. Installing again leaves an existing template alone.
export default function StarterPaperworkCard({ onInstalled }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function install() {
    setBusy(true); setError('')
    try {
      const data = await installIntakeStarterDocuments()
      setResult(data.documents || [])
      if (onInstalled) await onInstalled()
    } catch { setError('Could not add the standard client paperwork. Try again, or create the templates by hand.') }
    finally { setBusy(false) }
  }

  return (
    <section aria-labelledby="starter-paperwork-heading" className="rounded-xl border border-brand-line bg-brand-surface-2 p-4 shadow-sm">
      <h2 id="starter-paperwork-heading" className="flex items-center gap-2 text-sm font-semibold text-brand-ink">
        <FileCheck2 size={16} aria-hidden="true" /> Standard client paperwork
      </h2>
      <p className="mt-1 text-sm text-brand-muted">
        The fee agreement and client intake form every new client receives. Both arrive as drafts for attorney review and approval; your own templates of the same name are left as they are.
      </p>
      <button type="button" className="mt-3 text-sm font-semibold text-brand-accent-2 hover:underline" disabled={busy} onClick={install}>
        {busy ? 'Adding…' : 'Add the standard client paperwork'}
      </button>
      {result && <ul className="mt-2 space-y-1 text-sm text-brand-muted">
        {result.map((doc) => <li key={doc.key}>{doc.title} — {doc.created ? 'added as a draft for attorney review' : 'already in your library'}</li>)}
      </ul>}
      {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
    </section>
  )
}
