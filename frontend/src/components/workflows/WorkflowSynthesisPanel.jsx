import { useCallback, useEffect, useState } from 'react'
import api from '../../api'

const ROOT = '/workflow-config/synthesis'
const fields = [
  ['matter_key', 'Matter key'], ['title', 'Task title'], ['due_date', 'Task due date'],
  ['opened_at', 'Matter opened date'], ['matter_type', 'Matter type'],
  ['practice_area', 'Practice area'], ['matter_name', 'Matter name to remove from titles'],
  ['assignee_role', 'Assignment role'], ['task_type', 'Task type'],
]
const messageFor = error => typeof error?.response?.data?.detail === 'string'
  ? error.response.data.detail : 'Workflow history could not be processed. Please try again.'

function HistoryImport({ onChanged }) {
  const [provider, setProvider] = useState('clio')
  const [tasks, setTasks] = useState(null)
  const [matters, setMatters] = useState(null)
  const [preview, setPreview] = useState(null)
  const [mapping, setMapping] = useState({ tasks: {}, matters: {} })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const fileChanged = (setter, file) => { setter(file); setPreview(null); setMapping({ tasks: {}, matters: {} }); setMessage('') }
  const submit = async (importing = false) => {
    setBusy(true); setMessage('')
    const data = new FormData()
    data.append('tasks', tasks)
    if (matters) data.append('matters', matters)
    if (importing) {
      data.append('provider', provider)
      data.append('mapping', JSON.stringify(mapping))
      data.append('expected_fingerprint', preview.fingerprint)
    }
    try {
      const response = await api.post(`${ROOT}/history/${importing ? 'import' : 'preview'}`, data)
      if (importing) {
        setMessage(response.data.reused ? 'This history is already staged.' : `${response.data.imported_rows} history rows staged. Draft analysis is queued.`)
        setPreview(null)
        onChanged()
      } else setPreview(response.data)
    } catch (error) { setMessage(messageFor(error)) }
    finally { setBusy(false) }
  }
  return <details className="mt-4 rounded border border-brand-line p-3">
    <summary className="cursor-pointer font-medium">Import Clio or Tabs3 workflow history</summary>
    <p className="my-2 text-sm text-brand-muted">Upload a task/calendar CSV and, if needed, a separate matter CSV. Match their matter keys below. Each file supports 2 MiB and 5,000 rows. This imports scheduling history for draft suggestions; contacts, matters, and accounting records are managed by the migration tools.</p>
    <label className="block text-sm">Source system <select value={provider} onChange={e => setProvider(e.target.value)} disabled={busy} className="rounded border p-2"><option value="clio">Clio</option><option value="tabs3">Tabs3</option></select></label>
    <label className="my-2 block text-sm">Task or calendar CSV <input type="file" accept=".csv,text/csv" disabled={busy} onChange={e => fileChanged(setTasks, e.target.files[0])} /></label>
    <label className="my-2 block text-sm">Matter CSV (optional) <input type="file" accept=".csv,text/csv" disabled={busy} onChange={e => fileChanged(setMatters, e.target.files[0])} /></label>
    <button type="button" className="btn-secondary" disabled={!tasks || busy} onClick={() => submit(false)}>Read column names</button>
    {preview && <div className="mt-3 space-y-3">
      <p className="text-sm">{preview.task_rows} task rows and {preview.matter_rows} matter rows. Only mapped scheduling fields are retained. Unmapped notes and descriptions are discarded.</p>
      {['tasks', 'matters'].filter(kind => kind === 'tasks' || preview.matter_headers.length).map(kind => <fieldset key={kind} className="grid gap-2 sm:grid-cols-2">
        <legend className="font-medium">{kind === 'tasks' ? 'Task' : 'Matter'} columns</legend>
        {fields.filter(([key]) => kind === 'tasks' || !['title', 'due_date', 'task_type', 'assignee_role'].includes(key)).map(([key, label]) => <label key={key} className="text-sm">{kind === 'tasks' ? 'Task' : 'Matter'} CSV: {label}
          <select className="block w-full rounded border p-2" value={mapping[kind][key] || ''} disabled={busy}
            onChange={e => setMapping(current => ({ ...current, [kind]: { ...current[kind], [key]: e.target.value } }))}>
            <option value="">Not mapped</option>
            {(kind === 'tasks' ? preview.task_headers : preview.matter_headers).map(header => <option key={header} value={header}>{header}</option>)}
          </select>
        </label>)}
      </fieldset>)}
      <p className="text-sm text-brand-muted">Task matter key, title, due date, and a matching matter-open date are required. Dates may use YYYY-MM-DD or MM/DD/YYYY. Assignment roles use matter_owner, attorney_of_record, template_applier, or unassigned; other values stay unassigned.</p>
      <button type="button" className="btn-primary" disabled={busy} onClick={() => submit(true)}>{busy ? 'Staging…' : 'Stage history and prepare drafts'}</button>
    </div>}
    {message && <p role="status" className="mt-2 text-sm">{message}</p>}
  </details>
}

function Proposal({ item, canManage, onChanged }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const decline = async () => {
    setBusy(true); setMessage('')
    try {
      await api.post(`${ROOT}/${item.id}/decline`, { expected_proposal_sha256: item.proposal_sha256, reason })
      await onChanged()
    } catch (error) { setMessage(messageFor(error)) }
    finally { setBusy(false) }
  }
  return <article className="mt-3 rounded border border-brand-line p-3">
    <h3 className="font-medium">{item.name}</h3>
    <p className="text-sm">{item.status === 'rejected' ? 'Declined' : `${item.version_status} version · ${item.rule_status} rule`}{item.baseline ? ' · Amendment to existing workflow' : ''}</p>
    <p className="mt-2 text-sm">{item.evidence.matter_count} distinct matters · {item.evidence.cohort} history</p>
    <p className="text-sm text-brand-muted">{item.evidence.warning}</p>
    <div className="mt-2 overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr><th>Suggested task</th><th>Due after opening</th><th>Assignment</th><th>Observed matters</th></tr></thead>
      <tbody>{item.configuration.definition.checklist.map(task => {
        const evidence = item.evidence.items.find(row => row.item_key === task.item_key)
        return <tr key={task.item_key}><td className="py-1 pr-3">{task.title}</td><td>{task.due_offset_days} days</td><td>{task.assignee_role.replaceAll('_', ' ')}</td><td>{evidence ? `${evidence.matter_count} (${Math.round(evidence.sample_share * 100)}%)` : 'Preserved from approved workflow'}</td></tr>
      })}</tbody>
    </table></div>
    <details className="mt-2 text-sm"><summary className="cursor-pointer">Evidence and source references</summary>
      {item.evidence.items.map(row => <div className="my-2" key={row.item_key}>
        <p>Observed timing: {row.due_offset_range.join('–')} days; roles: {Object.entries(row.assignee_counts).map(([role, count]) => `${role}: ${count}`).join(', ')}.</p>
        <p>Timing source: {Object.entries(row.timing_basis_counts || {}).map(([basis, count]) => `${basis}: ${count}`).join(', ')}.</p>
        <p>Review routing: {Object.entries(row.review_policy_counts || {}).map(([policy, count]) => `${policy}: ${count}`).join(', ') || 'No review routing observed'}.</p>
        <ul className="break-all">{(row.evidence_refs || []).map((source, index) => <li key={index}>{source.kind} {source.id} · SHA-256 {source.sha256}{(source.context || []).map(context => <span key={context.id}> · Related source {context.id} ({context.sha256})</span>)}</li>)}</ul>
      </div>)}
      <p className="break-all">Configuration SHA-256: {item.definition_sha256}</p>
    </details>
    {item.status === 'rejected' ? <p className="mt-2 text-sm">Reason: {item.rejection_reason}. This pattern will not be suggested again.</p> : item.version_status === 'draft' && <>
      <a className="mt-2 inline-block text-sm underline" href={`#workflow-version-${item.template_version_id}`}>Review this draft in Templates and versions</a>
      {canManage && <div className="mt-3 flex flex-wrap items-end gap-2"><label className="text-sm">Reason to decline this pattern<input className="block rounded border p-2" value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} disabled={busy} /></label><button type="button" className="btn-secondary" disabled={!reason.trim() || busy} onClick={decline}>Decline pattern</button></div>}
    </>}
    {message && <p role="alert" className="mt-2 text-sm">{message}</p>}
  </article>
}

export default function WorkflowSynthesisPanel({ user, onChanged, onboarding = false }) {
  const caps = user?.capabilities || []
  const canManage = caps.includes('manage_workflows') && caps.includes('manage_matters')
  const canReview = caps.includes('manage_workflows') || caps.includes('approve_legal_work')
  const [data, setData] = useState({ items: [], jobs: [], next_offset: null })
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const refresh = useCallback(async () => {
    try { const response = await api.get(ROOT); setData(response.data) }
    catch (error) { setMessage(messageFor(error)) }
  }, [])
  const changed = useCallback(async () => { await refresh(); await onChanged?.() }, [refresh, onChanged])
  useEffect(() => { if (canReview) refresh() }, [canReview, refresh])
  const pending = data.jobs.some(job => ['pending', 'running'].includes(job.status))
  useEffect(() => {
    if (!pending) return undefined
    const timer = setInterval(changed, 3000)
    return () => clearInterval(timer)
  }, [pending, changed])
  if (!canReview) return null
  const analyze = async () => {
    setBusy(true); setMessage('')
    try { await api.post(`${ROOT}/analyze`, { request_id: crypto.randomUUID() }); await changed(); setMessage('Firm history analysis queued.') }
    catch (error) { setMessage(messageFor(error)) }
    finally { setBusy(false) }
  }
  const more = async () => {
    try { const response = await api.get(ROOT, { params: { offset: data.next_offset } }); setData(current => ({ ...response.data, items: [...current.items, ...response.data.items] })) }
    catch (error) { setMessage(messageFor(error)) }
  }
  return <section className="my-4 rounded-xl border border-brand-line p-4" aria-label="Workflow suggestions from firm history">
    <h2 className="font-semibold">Workflow suggestions from firm history</h2>
    <p className="mt-1 text-sm text-brand-muted">Find repeated tasks, document-template use, review routing, and communication timing in the last 60 days and staged imports. Suggestions require at least three distinct matters. Every new template and rule starts as a draft; an amendment takes effect only when its version is approved.</p>
    {canManage && <button type="button" className="btn-primary mt-3" disabled={busy || pending} onClick={analyze}>{pending ? 'Analyzing history…' : 'Analyze firm history'}</button>}
    <button type="button" className="btn-secondary ml-2 mt-3" onClick={changed}>Refresh suggestions</button>
    {canManage && caps.includes('admin_settings') && <HistoryImport onChanged={changed} />}
    {message && <p role="status" className="mt-2 text-sm">{message}</p>}
    {data.jobs[0] && !pending && <p className="mt-2 text-sm">Latest analysis: {data.jobs[0].status === 'failed' ? data.jobs[0].failure : data.jobs[0].result?.outcome?.replaceAll('_', ' ') || data.jobs[0].status}. {data.jobs[0].result?.observed_records ?? 0} observations.</p>}
    {!data.items.length && <p className="mt-3 text-sm text-brand-muted">No suggestions yet. Analyze existing history or stage an export to get started.</p>}
    {onboarding && data.items.length > 0 ? <p className="mt-3 text-sm">{data.items.length} suggestions are ready. Open Workflow settings after setup to inspect their evidence and review each draft.</p> : data.items.map(item => <Proposal key={item.id} item={item} canManage={canManage} onChanged={changed} />)}
    {data.next_offset !== null && <button type="button" className="btn-secondary mt-3" onClick={more}>Load older suggestions</button>}
  </section>
}
