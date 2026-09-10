import { useEffect, useState } from 'react'
import { updatePlatformTenant } from '../api'
const EMPTY = []
const PANELS = { documents: 'Documents', activity: 'Activity', team: 'Team', workflow: 'Workflow', correspondence: 'Correspondence', portal: 'Client Portal', billing: 'Billing', chat: 'Matter assistant' }
export default function TenantPanelSettings({ tenantId, hiddenPanels = EMPTY, platformKey, onSaved }) {
  const [hidden, setHidden] = useState(hiddenPanels)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => { setHidden(hiddenPanels) }, [hiddenPanels])
  async function save() {
    setBusy(true); setMessage('')
    try { await updatePlatformTenant(platformKey, tenantId, { hidden_matter_panels: hidden }); onSaved?.(hidden); setMessage('Saved. Users receive the policy when their session refreshes or they sign in again.') }
    catch { setMessage('Could not save panel settings. Try again.') }
    finally { setBusy(false) }
  }
  return <section className="mt-4 border-t border-brand-line pt-4" aria-label="Tenant feature panels">
    <h4 className="text-sm font-semibold">Feature panels</h4><p className="my-2 text-xs text-brand-muted">Choose panels shown in matters. This changes presentation for this tenant, not permissions or running workflows. Overview, tasks, and settings remain available.</p>
    <div className="grid grid-cols-2 gap-2">{Object.entries(PANELS).map(([key, label]) => <label key={key} className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={busy} checked={!hidden.includes(key)} onChange={() => setHidden(hidden.includes(key) ? hidden.filter(item => item !== key) : [...hidden, key])} />{label}</label>)}</div>
    <p className="my-2 text-xs">Shown: {Object.entries(PANELS).filter(([key]) => !hidden.includes(key)).map(([, label]) => label).join(', ') || 'Overview and settings only'}</p>
    <button type="button" disabled={busy} onClick={() => setHidden([])} className="mr-3 text-sm underline">Reset to default</button><button type="button" disabled={busy} onClick={save} className="rounded border px-3 py-2 text-sm">Save panel settings</button>
    {message && <p role="status" className="mt-2 text-sm">{message}</p>}
  </section>
}
