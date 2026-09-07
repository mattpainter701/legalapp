import { useEffect, useState } from 'react'
import { listRoles, createRole, updateRole, deleteRole } from '../../api'
import { VIEW_ITEMS, VIEW_PRESETS } from '../../navigation'

const CAPABILITIES = [
  'manage_users', 'manage_roles', 'manage_billing', 'view_billing',
  'manage_matters', 'manage_intake', 'manage_documents', 'manage_workflows',
  'approve_legal_work',
  'manage_integrations', 'admin_settings', 'use_premium_ai',
]

export default function RolesTab() {
  const [roles, setRoles] = useState([])
  const [name, setName] = useState('')
  const [caps, setCaps] = useState([])
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)
  const [paths, setPaths] = useState(null)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')

  const load = () => listRoles().then(setRoles).catch(() => setError('Failed to load roles'))
  useEffect(() => { load() }, [])

  const toggleCap = (c) =>
    setCaps((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))

  const submit = async (e) => {
    e.preventDefault()
    setError(''); setNotice(''); setSaving(true)
    try {
      const body = { name: name.trim(), capabilities: caps, description: editing?.description || null, navigation_paths: paths }
      if (editing) await updateRole(editing.id, body)
      else await createRole(body)
      setNotice('Role saved. Assign it to staff in Users. Updated views apply when users reload their workspace.')
      setName(''); setCaps([]); setEditing(null); setPaths(null); await load()
    } catch (err) {
      setError(typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : 'Failed to save role')
    } finally { setSaving(false) }
  }

  return (
    <div className="space-y-6">
      <div><h2 className="font-serif text-xl font-bold">Roles &amp; view profiles</h2><p className="mt-2 text-sm text-brand-muted">Choose the functions each role sees. Views simplify navigation; capabilities and licenses still control access. Staff with multiple configured roles see the combined functions. Roles without a view profile do not add functions to that combined view.</p></div>
      {notice && <p role="status" className="text-sm text-brand-muted">{notice}</p>}
      {error && <div role="alert" className="text-red-600 text-sm">{error}</div>}
      <form onSubmit={submit} className="space-y-3 rounded-xl border border-brand-line p-4">
        <h3 className="font-semibold">{editing ? `Edit ${editing.name}` : 'Create role'}</h3>
        <fieldset disabled={saving} className="space-y-3">
        <legend className="sr-only">Role settings</legend>
        <input aria-label="Role name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Role name (e.g. Paralegal)" className="border px-3 py-2 rounded w-full" />
        <label className="block text-sm font-medium">View starting point
          <select aria-label="View starting point" value="" onChange={(e) => {
            const preset = e.target.value
            setPaths(VIEW_PRESETS[preset] ? [...VIEW_PRESETS[preset]] : preset === 'none' ? [] : null)
            if (!name && VIEW_PRESETS[preset]) setName(preset)
          }} className="mt-1 block w-full rounded border border-brand-line px-3 py-2">
            <option value="" disabled>Choose a starting point…</option>
            <option value="all">No view restriction</option>
            {Object.keys(VIEW_PRESETS).map((preset) => <option key={preset}>{preset}</option>)}
            <option value="none">Hide all functions</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={paths !== null} onChange={(e) => setPaths(e.target.checked ? VIEW_ITEMS.map((item) => item.path) : null)} />Configure a view profile for this role</label>
        {paths !== null && <fieldset className="grid grid-cols-1 sm:grid-cols-2 gap-2 rounded-lg bg-brand-bg-soft p-3">
          <legend className="text-sm font-semibold">Visible functions</legend>
          {VIEW_ITEMS.map((item) => <label key={item.path} className="flex items-center gap-2 text-sm min-h-9">
            <input type="checkbox" checked={paths.includes(item.path)} onChange={() => setPaths((current) => current.includes(item.path) ? current.filter((path) => path !== item.path) : [...current, item.path])} />{item.label}
          </label>)}
        </fieldset>}
        <p className="text-xs text-brand-muted">Administration, profile, and sign out remain available to authorized staff. Users can hide and reorder their available functions with the navigation gear.</p>
        <h4 className="text-sm font-semibold">Access capabilities</h4>
        <p className="text-xs text-brand-muted">View starting points only choose visible functions. Set the access capabilities needed for this role separately.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {CAPABILITIES.map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={caps.includes(c)} onChange={() => toggleCap(c)} />
              {c}
            </label>
          ))}
        </div>
        <button type="submit" className="bg-brand-ink text-white px-4 py-2 rounded">
          {saving ? 'Saving…' : editing ? 'Save role' : 'Create role'}
        </button>
        {editing && <button type="button" className="ml-3 text-sm underline" onClick={() => { setEditing(null); setName(''); setCaps([]); setPaths(null) }}>Cancel edit</button>}
        </fieldset>
      </form>
      <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] text-sm">
        <thead><tr><th className="text-left">Role</th><th className="text-left">View profile</th><th className="text-left">Capabilities</th><th /></tr></thead>
        <tbody>
          {roles.map((r) => (
            <tr key={r.id} className="border-t">
              <td className="py-2">{r.name}{r.is_system && ' (system)'}</td>
              <td>{r.navigation_paths == null ? 'No view restriction' : `${r.navigation_paths.length} functions`}</td>
              <td>{(r.capabilities || []).join(', ')}</td>
              <td className="text-right">
                <button type="button" className="mr-3 underline" aria-label={`Edit ${r.name}`} onClick={() => { setEditing(r); setName(r.name); setCaps(r.capabilities || []); setPaths(r.navigation_paths ?? null); setNotice(''); setError('') }}>Edit</button>
                {!r.is_system && (
                  <button disabled={saving} onClick={() => deleteRole(r.id).then(load).catch(() => setError('Failed to delete role'))} className="text-red-600">Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
}
