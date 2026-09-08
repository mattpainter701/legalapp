import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listWorkflowRuns, resumeWorkflowRun, cancelWorkflowRun, reconcileWorkflowRunCloud } from '../../api'

const labels = {
  prepare_document: 'Prepare a matter document',
  prepare_document_and_correspondence: 'Prepare document and correspondence',
  coordinate_matter_work: 'Coordinate matter work',
}
const readable = value => String(value || '').replaceAll('_', ' ')

function RunCard({ run, onChanged }) {
  const [inputs, setInputs] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [providerId, setProviderId] = useState('')
  const [reason, setReason] = useState('')
  const current = run.steps[run.next_step]
  const paused = ['awaiting_input', 'awaiting_review', 'blocked'].includes(run.status)
  const finished = ['completed', 'cancelled', 'failed'].includes(run.status)
  async function reconcile() {
    setBusy(true); setError('')
    try {
      await reconcileWorkflowRunCloud(run.run_id, { expected_version: run.version, provider_object_id: providerId.trim(), reason: reason.trim() })
      await onChanged()
    } catch (err) { setError(err.response?.data?.detail?.message || 'The cloud object could not be verified.') }
    finally { setBusy(false) }
  }
  async function act(cancel = false) {
    setBusy(true); setError('')
    try {
      if (cancel) await cancelWorkflowRun(run.run_id, run.version)
      else {
        const missing = {}
        for (const field of current?.required_inputs || []) {
          if (!inputs[field]?.trim()) continue
          if (field.endsWith('_ids')) missing[field] = inputs[field].split(/[\n,]+/).map(v => v.trim()).filter(Boolean)
          else if (field === 'variables') missing[field] = Object.fromEntries(inputs[field].split('\n').filter(Boolean).map(line => {
            const split = line.indexOf('=')
            if (split < 1) throw new Error('Enter each variable as name=value on its own line.')
            return [line.slice(0, split).trim(), line.slice(split + 1)]
          }))
          else missing[field] = inputs[field]
        }
        await resumeWorkflowRun(run.run_id, { expected_version: run.version, missing_arguments: missing })
      }
      setInputs({}); await onChanged()
    } catch (err) { setError(err.response?.data?.detail?.message || err.message || 'Unable to update this run.') }
    finally { setBusy(false) }
  }
  return <article className="rounded-lg border border-brand-line p-4 space-y-3">
    <div className="flex flex-wrap justify-between gap-2"><h3 className="font-semibold">{labels[run.objective] || 'Matter workflow'}</h3><span className="capitalize">{readable(run.status)}</span></div>
    <p className="text-sm text-brand-muted">Started {new Date(run.created_at).toLocaleString()} through {run.origin_channel === 'workspace_mcp' ? 'a connected assistant' : 'LawHand'} · <Link className="underline" to={`/matters/${run.matter_id}?tab=workflow`}>Open matter</Link></p>
    <ol className="space-y-2">{run.steps.map((step, index) => <li key={step.step_key} className="text-sm">
      <span>{index + 1}. {readable(step.capability)} — {readable(step.status)}</span>
      {step.task_id && <> · <Link className="underline" to={`/tasks/${step.task_id}`}>Open review task</Link></>}
      {step.approval_id && <span> · Approval recorded</span>}
    </li>)}</ol>
    {run.status === 'awaiting_review' && <p className="text-sm">Complete the review task in LawHand, then continue. Delivery, when requested, must be confirmed before the next step.</p>}
    {run.status === 'reconciliation_required' && <p className="text-sm">The provider outcome needs reconciliation. This run will retain the original artifact and will not repeat the write.</p>}
    {run.can_continue && run.status === 'reconciliation_required' && current?.storage_operation_id && !current?.result && <fieldset className="space-y-2 text-sm">
      <legend className="font-medium">Verify the existing cloud document</legend>
      <p>Locate the original file in the matter’s cloud folder. LawHand verifies its folder and exact bytes before continuing.</p>
      <label className="block">Provider file ID<input className="block w-full border rounded p-2" value={providerId} onChange={e => setProviderId(e.target.value)} /></label>
      <label className="block">Reconciliation note<textarea className="block w-full border rounded p-2" value={reason} onChange={e => setReason(e.target.value)} maxLength={1000} /></label>
      <button disabled={busy || !providerId.trim() || !reason.trim()} onClick={reconcile} className="border rounded px-3 py-2">Verify existing file</button>
    </fieldset>}
    {run.failure_code && <p className="text-sm">Reason: {readable(run.failure_code)}</p>}
    {run.can_continue && run.status === 'awaiting_input' && <fieldset className="space-y-2"><legend className="font-medium">Information needed</legend>{(current?.required_inputs || []).map(field => <label key={field} className="block text-sm capitalize">{readable(field)}
      <textarea aria-label={readable(field)} value={inputs[field] || ''} onChange={e => setInputs({ ...inputs, [field]: e.target.value })} className="block w-full rounded border border-brand-line bg-brand-surface p-2" rows={field === 'body' ? 5 : 2} />
      {field === 'variables' && <span className="normal-case">Enter each variable as name=value on its own line.</span>}
    </label>)}</fieldset>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {run.can_continue && !finished && <div className="flex gap-3">
      {paused && <button disabled={busy} onClick={() => act()} className="rounded bg-brand-ink text-white px-3 py-2">Continue run</button>}
      <button disabled={busy} onClick={() => act(true)} className="rounded border border-brand-line px-3 py-2">Cancel remaining steps</button>
    </div>}
    <details className="text-xs text-brand-muted"><summary>Run evidence</summary><p className="break-all">Run {run.run_id} · Plan {run.plan_sha256}</p><ol>{run.events.map(event => <li key={event.sequence}>{new Date(event.at).toLocaleString()} — {readable(event.type)}</li>)}</ol></details>
  </article>
}

export default function WorkflowRunsPanel({ matterId }) {
  const [data, setData] = useState({ items: [], next_offset: null })
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const load = useCallback(async () => {
    try {
      const result = await listWorkflowRuns({ matter_id: matterId, offset })
      if (!Array.isArray(result?.items)) throw new Error('Invalid workflow response')
      setData(result); setError('')
    }
    catch { setError('Unable to load workflow runs.') }
  }, [matterId, offset])
  useEffect(() => { load() }, [load])
  const running = data.items.some(run => ['queued', 'running'].includes(run.status))
  useEffect(() => { if (!running) return; const timer = setInterval(load, 3000); return () => clearInterval(timer) }, [running, load])
  return <section className="space-y-4 mt-6" aria-label="Workflow runs">
    <div className="flex justify-between items-center"><h2 className="text-lg font-semibold">Workflow runs</h2><button onClick={load} className="underline text-sm">Refresh runs</button></div>
    <p className="text-sm text-brand-muted">Start a bounded workflow in LawHand Chat or your connected assistant. Review its progress and continue paused work here.</p>
    {error && <p role="alert">{error}</p>}
    {!error && !data.items.length && <p className="text-sm">No workflow runs yet.</p>}
    {data.items.map(run => <RunCard key={run.run_id} run={run} onChanged={load} />)}
    <div className="flex gap-4">{offset > 0 && <button onClick={() => setOffset(Math.max(0, offset - 20))}>Newer runs</button>}{data.next_offset !== null && <button onClick={() => setOffset(data.next_offset)}>Older runs</button>}</div>
  </section>
}
