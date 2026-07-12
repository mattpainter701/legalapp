import React, { useState, useEffect } from 'react'
import { getMatterParties, addMatterParty, removeMatterParty, getContacts } from '../api'
import { Users, Plus, Trash2, Star } from 'lucide-react'

const ROLES = [
  'client',
  'opposing_party',
  'counsel',
  'witness',
  'expert',
  'other',
]

function RoleBadge({ role }) {
  const colors = {
    client: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    opposing_party: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    counsel: 'bg-blue-100 text-blue-800 border-blue-200',
    witness: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    expert: 'bg-purple-100 text-purple-800 border-purple-200',
    other: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }
  const cls = colors[role] || colors.other
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}
    >
      {role?.replace(/_/g, ' ') || 'other'}
    </span>
  )
}

export default function MatterPartiesTab({ matterId }) {
  const [parties, setParties] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [contacts, setContacts] = useState([])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ contact_id: '', role: 'client', is_primary: false, notes: '' })
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState(null)

  const inputClasses =
    'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelClasses =
    'block text-[11px] font-bold text-brand-muted font-sans uppercase tracking-widest mb-1.5'

  useEffect(() => {
    setLoading(true)
    getMatterParties(matterId)
      .then((data) => {
        setParties(data.items || [])
        setTotal(data.total || 0)
      })
      .catch(() => setError('Failed to load parties.'))
      .finally(() => setLoading(false))
  }, [matterId])

  useEffect(() => {
    getContacts({ active_only: true, limit: 200 })
      .then((data) => setContacts(data.items || data.contacts || []))
      .catch(() => {})
  }, [])

  const handleAdd = async () => {
    if (!form.contact_id) return
    setAdding(true)
    setAddError(null)
    try {
      const party = await addMatterParty(matterId, {
        matter_id: matterId,
        contact_id: form.contact_id,
        role: form.role,
        is_primary: form.is_primary,
        notes: form.notes || null,
      })
      setParties((prev) => [...prev, party])
      setTotal((t) => t + 1)
      setForm({ contact_id: '', role: 'client', is_primary: false, notes: '' })
      setShowForm(false)
    } catch {
      setAddError('Failed to add party.')
    } finally {
      setAdding(false)
    }
  }

  const handleRemove = async (partyId) => {
    try {
      await removeMatterParty(matterId, partyId)
      setParties((prev) => prev.filter((p) => p.id !== partyId))
      setTotal((t) => t - 1)
    } catch {
      // silent — user can retry
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 text-brand-rose text-sm font-sans">
        {error}
      </div>
    )
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
        <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
          <Users size={20} className="text-brand-accent" /> Parties
          {total > 0 && (
            <span className="ml-1 text-[13px] font-sans font-normal text-brand-muted">
              ({total})
            </span>
          )}
        </h2>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm"
        >
          <Plus size={16} /> Add Party
        </button>
      </div>

      {showForm && (
        <div className="p-6 bg-brand-bg border-b border-brand-line">
          <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">
            Add Party
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
            <div>
              <label htmlFor="matterpartiestab-contact" className={labelClasses}>Contact</label>
              <select id="matterpartiestab-contact"
                value={form.contact_id}
                onChange={(e) => setForm((p) => ({ ...p, contact_id: e.target.value }))}
                className={inputClasses}
              >
                <option value="">— Select contact —</option>
                {contacts.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.display_name || `${c.first_name || ''} ${c.last_name || ''}`.trim() || c.organization_name || c.id}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="matterpartiestab-role" className={labelClasses}>Role</label>
              <select id="matterpartiestab-role"
                value={form.role}
                onChange={(e) => setForm((p) => ({ ...p, role: e.target.value }))}
                className={inputClasses}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                  </option>
                ))}
              </select>
            </div>
            <div className="md:col-span-2">
              <label htmlFor="matterpartiestab-notes-optional" className={labelClasses}>Notes (optional)</label>
              <input id="matterpartiestab-notes-optional"
                type="text"
                value={form.notes}
                onChange={(e) => setForm((p) => ({ ...p, notes: e.target.value }))}
                placeholder="e.g., lead counsel, retained 2024-01"
                className={inputClasses}
              />
            </div>
            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                id="is_primary"
                checked={form.is_primary}
                onChange={(e) => setForm((p) => ({ ...p, is_primary: e.target.checked }))}
                className="w-4 h-4 rounded border-brand-line text-brand-accent focus:ring-brand-accent"
              />
              <label
                htmlFor="is_primary"
                className="text-[13px] font-sans text-brand-ink-2 cursor-pointer"
              >
                Primary party
              </label>
            </div>
          </div>
          {addError && (
            <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">
              {addError}
            </p>
          )}
          <div className="flex gap-3 justify-end">
            <button
              onClick={() => setShowForm(false)}
              className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans hover:text-brand-ink transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleAdd}
              disabled={adding || !form.contact_id}
              className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all shadow-sm"
            >
              {adding ? 'Adding…' : 'Add Party'}
            </button>
          </div>
        </div>
      )}

      <div className="p-6">
        {parties.length === 0 ? (
          <div className="text-center py-12">
            <Users size={32} className="mx-auto text-brand-line-2 mb-3" strokeWidth={1.5} />
            <p className="text-brand-ink font-serif text-lg font-bold mb-1">No parties added</p>
            <p className="text-brand-muted text-sm font-sans">
              Add clients, opposing counsel, witnesses, and experts.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-[14px] font-sans">
            <thead>
              <tr className="border-b border-brand-line">
                <th className="text-left text-[11px] font-bold text-brand-muted uppercase tracking-widest pb-3 pr-4">
                  Contact
                </th>
                <th className="text-left text-[11px] font-bold text-brand-muted uppercase tracking-widest pb-3 pr-4">
                  Role
                </th>
                <th className="text-left text-[11px] font-bold text-brand-muted uppercase tracking-widest pb-3 pr-4">
                  Notes
                </th>
                <th className="w-10 pb-3" />
              </tr>
            </thead>
            <tbody>
              {parties.map((party) => (
                <tr
                  key={party.id}
                  className="border-b border-brand-line/50 last:border-0 hover:bg-brand-bg-soft/40 transition-colors"
                >
                  <td className="py-3 pr-4">
                    <span className="font-medium text-brand-ink flex items-center gap-1.5">
                      {party.is_primary && (
                        <Star
                          size={13}
                          className="text-brand-amber fill-brand-amber flex-shrink-0"
                        />
                      )}
                      {party.contact_display_name || '—'}
                    </span>
                  </td>
                  <td className="py-3 pr-4">
                    <RoleBadge role={party.role} />
                  </td>
                  <td className="py-3 pr-4 text-brand-ink-2">
                    {party.notes || <span className="text-brand-line-2">—</span>}
                  </td>
                  <td className="py-3 text-right">
                    <button
                      onClick={() => handleRemove(party.id)}
                      className="p-1.5 text-brand-muted hover:text-brand-rose hover:bg-brand-rose/10 rounded-lg transition-colors"
                      title="Remove party"
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </div>
  )
}
