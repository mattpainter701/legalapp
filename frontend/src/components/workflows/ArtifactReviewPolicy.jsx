import { useEffect, useState } from 'react'
import api from '../../api'

export default function ArtifactReviewPolicy() {
  const [saved, setSaved] = useState(null)
  const [policy, setPolicy] = useState('staff_then_attorney')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    let active = true
    api.get('/artifact-reviews/policy').then(({ data }) => {
      if (active) { setSaved(data); setPolicy(data.policy) }
    }).catch(() => { if (active) setMessage('Document review policy could not be loaded.') })
    return () => { active = false }
  }, [])
  const save = async () => {
    if (!saved?.can_edit || busy) return
    setBusy(true)
    setMessage('')
    try {
      const { data } = await api.put('/artifact-reviews/policy', { policy, expected_policy: saved.policy })
      setSaved(data)
      setMessage('Review policy saved. Existing reviews keep their assigned reviewers.')
    } catch (error) {
      const detail = error.response?.data?.detail
      setMessage(typeof detail === 'string' ? detail : 'Review policy could not be saved.')
    } finally { setBusy(false) }
  }
  return <section className="rounded-xl border border-brand-line p-4" aria-label="Document review policy">
    <h2 className="font-semibold">Document review policy</h2>
    <p className="mt-1 text-sm text-brand-muted">New assistant document drafts follow this review sequence. Every approval covers an exact revision; client delivery requires its own approval.</p>
    {saved && <>
      <label className="mt-3 block text-sm">Review sequence
        <select className="ml-2 rounded border border-brand-line p-2" value={policy} onChange={event => setPolicy(event.target.value)} disabled={!saved.can_edit || busy}>
          <option value="staff_then_attorney">Staff, then attorney</option>
          <option value="attorney_only">Attorney only</option>
        </select>
      </label>
      {saved.can_edit && <button type="button" className="btn-primary mt-3" disabled={busy || policy === saved.policy} onClick={save}>{busy ? 'Saving…' : 'Save review policy'}</button>}
    </>}
    {message && <p role="status" className="mt-2 text-sm">{message}</p>}
  </section>
}
