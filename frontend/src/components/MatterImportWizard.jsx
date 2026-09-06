import { useState } from 'react'
import api, { getContacts, getMattersV2 } from '../api'

const inputClass = 'border border-brand-line rounded-lg px-3 py-2 w-full bg-white text-brand-ink'
const detail = (error) => typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : 'Import could not continue. Check the fields and connection, then retry.'
const postFile = (url, file, path) => {
  const form = new FormData()
  form.append('file', file)
  if (path) form.append('path', path)
  return api.post(url, form, { timeout: 0 }).then(r => r.data)
}

export function groupFiles(files, depth) {
  return files.map(file => ({ ...file, group: depth === 0 ? 'All selected files' : file.path.split('/').slice(0, depth).join('/') }))
}

export default function MatterImportWizard({ matterId, onComplete }) {
  const [selected, setSelected] = useState([])
  const [archive, setArchive] = useState(null)
  const [files, setFiles] = useState([])
  const [depth, setDepth] = useState(matterId ? 0 : 1)
  const [run, setRun] = useState(null)
  const [resumeId, setResumeId] = useState('')
  const [mappings, setMappings] = useState([])
  const [contacts, setContacts] = useState([])
  const [matters, setMatters] = useState([])
  const [former, setFormer] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState('')

  async function selectFiles(list, zip = false) {
    setBusy(true); setError('')
    try {
      const items = Array.from(list)
      let manifest
      if (zip) {
        if (items[0].size > 512 * 1024 * 1024) throw new Error('Use folder upload for ZIPs larger than 512 MiB.')
        manifest = (await postFile('/matter-imports/zip-preview', items[0])).files
      } else {
        if (items.length > 10000 || items.some(f => f.size > 64 * 1024 * 1024)) throw new Error('Choose at most 10,000 files, each 64 MiB or smaller.')
        manifest = []
        for (const file of items) {
          setProgress(`Checking ${manifest.length + 1} of ${items.length}`)
          const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
          manifest.push({ path: file.webkitRelativePath || file.name, size: file.size, sha256: Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('') })
        }
      }
      setFiles(manifest); setSelected(zip ? [] : items); setArchive(zip ? items[0] : null)
    } catch (e) { setError(e.response ? detail(e) : e.message) }
    finally { setBusy(false); setProgress('') }
  }

  async function prepare() {
    setBusy(true); setError('')
    try {
      const grouped = groupFiles(files, depth)
      // Keep the request identity when a response is lost, so Retry cannot create another batch.
      const id = resumeId || crypto.randomUUID()
      setResumeId(id)
      const next = (await api.post('/matter-imports', { id, files: grouped })).data
      const [c, m] = await Promise.all([getContacts({ limit: 200 }), matterId ? Promise.resolve({ matters: [] }) : getMattersV2({ limit: 200 })])
      setContacts(c.contacts || c.items || (Array.isArray(c) ? c : [])); setMatters(m.matters || m.items || [])
      setRun(next)
      setMappings(next.approval?.mappings || [...new Set(grouped.map(f => f.group))].map(group => ({ group, matter_id: matterId || null, contact_id: null, matter_name: group.split('/').pop(), first_name: '', last_name: '', organization_name: '', case_number: '', intake: 'review', exclude: false })))
      setFormer(next.approval?.former_addresses?.join(', ') || '')
    } catch (e) { setError(detail(e)) } finally { setBusy(false) }
  }

  async function resume() {
    setBusy(true); setError('')
    try {
      const next = (await api.get(`/matter-imports/${resumeId}`)).data
      if (next.status === 'review') { setError('Reselect the original files and choose Review matter mappings to finish this import.'); return }
      setRun(next); setMappings(next.approval.mappings); setFormer(next.approval.former_addresses.join(', '))
    } catch (e) { setError(detail(e)) } finally { setBusy(false) }
  }

  async function upload() {
    setBusy(true); setError('')
    try {
      let current = run
      if (current.status === 'review') {
        current = (await api.post(`/matter-imports/${run.id}/approve`, { confirm: true, mappings, former_addresses: former.split(',').map(v => v.trim()).filter(Boolean) })).data
        setRun(current)
      }
      if (archive) {
        setProgress('Importing ZIP. Completed files are saved; retrying skips them.')
        current = await postFile(`/matter-imports/${run.id}/zip`, archive)
      } else {
        const byPath = new Map(selected.map(f => [f.webkitRelativePath || f.name, f]))
        for (const entry of current.files) {
          if (['imported', 'duplicate', 'excluded'].includes(current.results?.[entry.path]?.status)) continue
          const file = byPath.get(entry.path)
          if (!file) throw new Error('Reselect the original source folder to upload the remaining files.')
          setProgress(`Importing ${entry.path}`)
          const result = await postFile(`/matter-imports/${run.id}/file`, file, entry.path)
          current = { ...current, results: { ...current.results, [entry.path]: result } }
          setRun(current)
        }
        current = (await api.get(`/matter-imports/${run.id}`)).data
      }
      setRun(current)
      onComplete?.(current)
    } catch (e) { setError(e.response ? detail(e) : e.message) }
    finally { setBusy(false); setProgress('') }
  }

  function edit(index, field, value) {
    setMappings(previous => previous.map((m, i) => i === index ? { ...m, [field]: value } : m))
  }
  return <section className="space-y-4 p-4" aria-label="Import existing matters">
    <h3 className="font-bold text-lg">{matterId ? 'Import files & emails' : 'Import existing matters'}</h3>
    <p>Choose a folder from your computer or USB drive, or a ZIP. Email files will appear in Correspondence. Historical imports send no messages.</p>
    <p className="text-sm">Folders: up to 10,000 files, 64 MiB per file. ZIP: up to 512 MiB compressed / 1 GiB expanded. Keep the source until the result is complete.</p>
    <label className="block">Select folder<input disabled={busy} className={inputClass} type="file" webkitdirectory="" multiple onChange={e => selectFiles(e.target.files)} /></label>
    <label className="block">Select ZIP<input disabled={busy} className={inputClass} type="file" accept=".zip" onChange={e => e.target.files.length && selectFiles(e.target.files, true)} /></label>
    {!run && <>
      <label className="block">Matter grouping<select className={inputClass} value={depth} disabled={busy} onChange={e => setDepth(Number(e.target.value))}><option value={0}>All files belong to one matter</option><option value={1}>One matter per top-level folder</option><option value={2}>One matter per folder inside the selected folder</option></select></label>
      <button type="button" className={inputClass} disabled={busy || !files.length} onClick={prepare}>Review matter mappings ({files.length} files)</button>
      <label className="block">Resume import ID<input className={inputClass} value={resumeId} onChange={e => setResumeId(e.target.value)} /></label>
      <button type="button" disabled={busy || !resumeId} onClick={resume}>Resume saved import</button>
    </>}
    {run && <>
      <p className="text-sm break-all">Import ID: {run.id}. Save this ID to resume after closing this page.</p>
      {run.status === 'review' && <>
        <label className="block">Former attorney email addresses (optional, comma separated)<input className={inputClass} value={former} onChange={e => setFormer(e.target.value)} /></label>
        {mappings.map((mapping, index) => <fieldset key={mapping.group} className="border rounded-lg p-3 space-y-2" disabled={busy}>
          <legend className="font-semibold break-all">{mapping.group} ({run.files.filter(f => f.group === mapping.group).length} files)</legend>
          <label><input type="checkbox" checked={mapping.exclude} onChange={e => edit(index, 'exclude', e.target.checked)} /> Exclude this group</label>
          {!mapping.exclude && <>
            {!matterId && <label className="block">Destination<select className={inputClass} value={mapping.matter_id || ''} onChange={e => edit(index, 'matter_id', e.target.value || null)}><option value="">Create new matter</option>{matters.map(m => <option key={m.id} value={m.id}>{m.matter_name}</option>)}</select></label>}
            {!mapping.matter_id && <>
              <label className="block">Matter title<input className={inputClass} value={mapping.matter_name} onChange={e => edit(index, 'matter_name', e.target.value)} /></label>
              <label className="block">Client<select className={inputClass} value={mapping.contact_id || ''} onChange={e => edit(index, 'contact_id', e.target.value || null)}><option value="">Create client</option>{contacts.map(c => <option key={c.id} value={c.id}>{c.full_name || c.organization_name || `${c.first_name} ${c.last_name}`}</option>)}</select></label>
              {!mapping.contact_id && <>{[['first_name', 'First name'], ['last_name', 'Last name'], ['organization_name', 'Organization (for an organization client)']].map(([key, label]) => <label className="block" key={key}>{label}<input className={inputClass} value={mapping[key]} onChange={e => edit(index, key, e.target.value)} /></label>)}</>}
              <label className="block">Case number<input className={inputClass} value={mapping.case_number} onChange={e => edit(index, 'case_number', e.target.value)} /></label>
              <label className="block">Intake<select className={inputClass} value={mapping.intake} onChange={e => edit(index, 'intake', e.target.value)}><option value="review">Review required</option><option value="existing">Existing engagement</option><option value="required">Fresh intake required — prepare after import</option></select></label>
            </>}
          </>}
        </fieldset>)}
      </>}
      <button type="button" className={inputClass} disabled={busy || run.status === 'complete'} onClick={upload}>{run.status === 'review' ? 'Confirm mappings & import' : 'Upload remaining files / retry failures'}</button>
      <p role="status">{run.status === 'complete' ? 'Import complete' : 'Import in progress'} · {Object.values(run.results || {}).filter(r => ['imported', 'duplicate', 'excluded'].includes(r.status)).length} / {run.files.length} accounted for</p>
      <ul className="max-h-60 overflow-auto text-sm">{Object.entries(run.results || {}).map(([path, result]) => <li key={path} className="break-all">{path}: {result.status}{result.error && ` — ${result.error}`}</li>)}</ul>
    </>}
    {progress && <p role="status">{progress}</p>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
  </section>
}
