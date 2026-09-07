import { useEffect, useMemo, useState } from 'react'
import {
  abandonStorageMigration,
  cutoverStorageMigration,
  getLatestStorageMigration,
  getStorageMigrationMatches,
  reconcileStorageMigration,
  retryStorageMigrationReindex,
  startStorageMigration,
} from '../api'

const PROVIDERS = {
  google_drive: 'Google Drive',
  onedrive: 'Microsoft OneDrive',
  sharepoint: 'Microsoft SharePoint',
}

function ErrorText({ error }) {
  return error ? <p className="mt-3 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-xs">{error}</p> : null
}

export default function StorageMigrationPanel({ primaryProvider, permissions, sharePointBinding }) {
  const [target, setTarget] = useState('')
  const [evidenceVersion, setEvidenceVersion] = useState(null)
  const [targetRootId, setTargetRootId] = useState('')
  const [targetDriveId, setTargetDriveId] = useState('')
  const [migration, setMigration] = useState(null)
  const [matches, setMatches] = useState([])
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const historical = migration && (migration.phase === 'complete' || migration.phase === 'abandoned')
  const canStartNew = !migration || migration.phase === 'abandoned' || (migration.phase === 'complete' && !migration.needs_reindex)
  const effectiveProvider = migration?.phase === 'complete' ? migration.target_provider : primaryProvider

  const refresh = async () => {
    setBusy('refresh')
    setError(null)
    try {
      const latest = await getLatestStorageMigration()
      if (!latest?.id) { setMigration(null); setMatches([]); return }
      setMigration(latest)
      setEvidenceVersion(latest.evidence_version || null)
      const rows = await getStorageMigrationMatches(latest.id)
      setMatches(Array.isArray(rows) ? rows : [])
    } catch (err) {
      if (err?.response?.status === 404) { setMigration(null); setMatches([]) }
      else setError(err?.response?.data?.detail || 'Unable to refresh storage migration status.')
    } finally { setBusy(null) }
  }

  useEffect(() => { refresh() }, [])
  useEffect(() => {
    if (target === 'sharepoint' && sharePointBinding) {
      setTargetRootId((value) => value || sharePointBinding.root_item_id || '')
      setTargetDriveId((value) => value || sharePointBinding.drive_id || '')
    }
  }, [target, sharePointBinding])

  const connected = useMemo(() => {
    const options = []
    if (permissions?.google?.connected) options.push('google_drive')
    if (permissions?.microsoft?.connected) {
      options.push('onedrive')
      if (sharePointBinding || permissions.microsoft.sharepoint_connected || permissions.microsoft.sharepoint?.connected) options.push('sharepoint')
    }
    return options.filter((provider) => provider !== effectiveProvider)
  }, [permissions, effectiveProvider, sharePointBinding])

  const counts = migration?.bucket_counts || { matched: 0, missing: 0, ambiguous: 0 }
  const unresolved = matches.filter((item) => item.bucket === 'missing' || item.bucket === 'ambiguous')
  const canCutover = migration?.phase === 'awaiting_confirmation'
    && Number(counts.missing || 0) === 0
    && Number(counts.ambiguous || 0) === 0
    && Boolean(evidenceVersion)
    && acknowledged

  const run = async (action, fn) => {
    setBusy(action)
    setError(null)
    try {
      const result = await fn()
      setMigration((current) => ({ ...current, ...result }))
      return result
    } catch (err) {
      setError(err?.response?.data?.detail || `Unable to ${action} storage migration.`)
      return null
    } finally { setBusy(null) }
  }

  const start = async (event) => {
    event.preventDefault()
    const result = await run('start', () => startStorageMigration({ target_provider: target, target_root_id: targetRootId.trim() || (target === 'sharepoint' ? sharePointBinding?.root_item_id : undefined), target_drive_id: targetDriveId.trim() || (target === 'sharepoint' ? sharePointBinding?.drive_id : undefined) }))
    if (result) setMatches([])
  }

  const reconcile = async () => {
    const result = await run('reconcile', async () => {
      const next = await reconcileStorageMigration(migration.id)
      setEvidenceVersion(next.evidence_version || null)
      const rows = await getStorageMigrationMatches(migration.id)
      setMatches(Array.isArray(rows) ? rows : [])
      return next
    })
    if (result) setAcknowledged(false)
  }

  const cutover = () => run('cutover', () => cutoverStorageMigration(migration.id, {
    evidence_version: evidenceVersion,
    acknowledged_policy: 'all-matters-and-documents-resolved',
  }))

  const abandon = async () => {
    const result = await run('abandon', () => abandonStorageMigration(migration.id))
    if (result) { setMigration(null); setMatches([]); setAcknowledged(false) }
  }

  const retryReindex = () => run('reindex', () => retryStorageMigrationReindex(migration.id))

  return <section className="bg-brand-surface border border-brand-line rounded-xl p-6">
    <div className="flex items-start justify-between gap-4 flex-wrap">
      <div>
        <h3 className="text-brand-ink font-sans text-base font-bold">Storage migration</h3>
        <p className="text-brand-ink-2 font-sans text-xs mt-1">Rebind existing matter folders and files to a connected provider. LawHand discovers and verifies; it does not copy files.</p>
      </div>
      {migration && <span className="px-2.5 py-1 rounded-lg bg-brand-bg-soft border border-brand-line text-brand-ink text-xs font-bold">{migration.phase}</span>}
    </div>

    {canStartNew && <form onSubmit={start} className="mt-4 grid grid-cols-1 md:grid-cols-[1fr_1fr_1fr_auto] gap-3 items-end">
      <label className="block"><span className="block text-xs font-bold text-brand-ink mb-1">Target connected root</span><select required value={target} onChange={(e) => setTarget(e.target.value)} disabled={!effectiveProvider || Boolean(busy)} className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink text-sm"><option value="">Choose provider</option>{connected.map((p) => <option key={p} value={p}>{PROVIDERS[p]}</option>)}</select></label>
      <label className="block"><span className="block text-xs font-bold text-brand-ink mb-1">Root folder ID <span className="font-normal text-brand-muted">(optional if already bound)</span></span><input value={targetRootId} onChange={(e) => setTargetRootId(e.target.value)} disabled={Boolean(busy)} placeholder={target === 'sharepoint' ? (sharePointBinding?.root_item_id || 'SharePoint root item ID') : 'Provider root ID'} className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink text-sm" /></label>
      {target === 'sharepoint' && <label className="block"><span className="block text-xs font-bold text-brand-ink mb-1">SharePoint drive ID</span><input value={targetDriveId} onChange={(e) => setTargetDriveId(e.target.value)} disabled={Boolean(busy)} placeholder={sharePointBinding?.drive_id || 'Drive ID'} className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink text-sm" /></label>}
      <button type="submit" disabled={!effectiveProvider || !target || Boolean(busy)} className="px-4 py-2 bg-brand-ink text-white rounded-lg text-sm font-bold disabled:opacity-50">{busy === 'start' ? 'Starting…' : 'Start migration'}</button>
    </form>}

    {!effectiveProvider && <p className="mt-3 text-xs text-amber-700">Set a primary cloud provider first. Auto storage selection does not identify a migration source.</p>}

    {migration && <>
      <div className="mt-5 grid grid-cols-3 gap-3">{[['Matched', counts.matched], ['Missing', counts.missing], ['Ambiguous', counts.ambiguous]].map(([label, value]) => <div key={label} className="bg-brand-bg border border-brand-line rounded-lg p-3"><div className="text-[11px] uppercase font-bold text-brand-ink-2">{label}</div><div className="text-lg font-bold text-brand-ink">{value || 0}</div></div>)}</div>
      <div className="mt-4 flex flex-wrap gap-2"><button type="button" onClick={reconcile} disabled={Boolean(busy) || historical} className="px-3 py-2 border border-brand-line rounded-lg text-xs font-bold text-brand-ink disabled:opacity-50">{busy === 'reconcile' ? 'Discovering…' : 'Reconcile connected root'}</button><button type="button" onClick={refresh} disabled={Boolean(busy)} className="px-3 py-2 border border-brand-line rounded-lg text-xs font-bold text-brand-ink disabled:opacity-50">{busy === 'refresh' ? 'Refreshing…' : 'Refresh status'}</button><button type="button" onClick={abandon} disabled={Boolean(busy) || historical} className="px-3 py-2 border border-red-200 text-red-700 rounded-lg text-xs font-bold disabled:opacity-50">Abandon</button>{migration.phase === 'complete' && migration.needs_reindex && <button type="button" onClick={retryReindex} disabled={Boolean(busy)} className="px-3 py-2 border border-amber-300 text-amber-800 rounded-lg text-xs font-bold disabled:opacity-50">{busy === 'reindex' ? 'Retrying…' : 'Retry reindex'}</button>}</div>
      {unresolved.length > 0 && <div className="mt-4 border border-amber-200 bg-amber-50 rounded-lg p-3"><p className="text-xs font-bold text-amber-800">Unresolved items</p><ul className="mt-2 max-h-40 overflow-auto space-y-1">{unresolved.map((item) => <li key={`${item.object_type}:${item.object_id}`} className="text-xs text-amber-900">{item.object_type} · {item.object_id} · {item.bucket} {item.matching_rung ? `(${item.matching_rung})` : ''}</li>)}</ul></div>}
      {migration.phase === 'awaiting_confirmation' && <div className="mt-4 border-t border-brand-line pt-4 space-y-3"><p className="text-xs text-brand-muted">Evidence revision: <span className="font-mono text-brand-ink">{evidenceVersion || 'not returned; reconcile again'}</span></p><label className="flex items-start gap-2 text-xs text-brand-ink"><input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} disabled={Number(counts.missing || 0) + Number(counts.ambiguous || 0) > 0 || Boolean(busy)} /> I reviewed the discovery evidence and confirm every matter and document is resolved.</label><button type="button" onClick={cutover} disabled={!canCutover || Boolean(busy)} className="px-4 py-2 bg-brand-ink text-white rounded-lg text-sm font-bold disabled:opacity-50">{busy === 'cutover' ? 'Cutting over…' : 'Confirm cutover'}</button></div>}
      {migration.needs_reindex && <p className="mt-3 text-xs text-amber-700 font-medium">Cutover complete. Reindex is pending.</p>}
      {migration.error_message && <p className="mt-3 text-xs text-red-700">Reindex error: {migration.error_message}</p>}
    </>}
    <ErrorText error={error} />
  </section>
}
