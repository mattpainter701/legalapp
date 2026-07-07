import { useEffect, useState } from 'react'
import { listRoles, createRole, deleteRole } from '../../api'

const CAPABILITIES = [
  'manage_users', 'manage_roles', 'manage_billing', 'view_billing',
  'manage_matters', 'manage_intake', 'manage_documents',
  'manage_integrations', 'admin_settings', 'use_premium_ai',
]

export default function RolesTab() {
  const [roles, setRoles] = useState([])
  const [name, setName] = useState('')
  const [caps, setCaps] = useState([])
  const [error, setError] = useState('')

  const load = () => listRoles().then(setRoles).catch(() => setError('Failed to load roles'))
  useEffect(() => { load() }, [])

  const toggleCap = (c) =>
    setCaps((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    try {
      await createRole({ name: name.trim(), capabilities: caps })
      setName(''); setCaps([]); load()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to create role')
    }
  }

  return (
    <div className="space-y-6">
      {error && <div className="text-red-600 text-sm">{error}</div>}
      <form onSubmit={submit} className="space-y-3">
        <input value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Role name (e.g. Paralegal)" className="border px-3 py-2 rounded w-full" />
        <div className="grid grid-cols-2 gap-2">
          {CAPABILITIES.map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={caps.includes(c)} onChange={() => toggleCap(c)} />
              {c}
            </label>
          ))}
        </div>
        <button type="submit" className="bg-brand-ink text-white px-4 py-2 rounded">
          Create role
        </button>
      </form>
      <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] text-sm">
        <thead><tr><th className="text-left">Role</th><th className="text-left">Capabilities</th><th /></tr></thead>
        <tbody>
          {roles.map((r) => (
            <tr key={r.id} className="border-t">
              <td className="py-2">{r.name}{r.is_system && ' (system)'}</td>
              <td>{(r.capabilities || []).join(', ')}</td>
              <td className="text-right">
                {!r.is_system && (
                  <button onClick={() => deleteRole(r.id).then(load)} className="text-red-600">Delete</button>
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
