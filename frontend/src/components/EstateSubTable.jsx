import React, { useState, useEffect, useCallback } from 'react'
import { format, parseISO } from 'date-fns'
import { Plus, Trash2, Pencil, X, Check } from 'lucide-react'
import { listEstateChildren, createEstateChild, updateEstateChild, deleteEstateChild } from '../api'
import { useConfirm } from './dialog/ConfirmProvider'

/**
 * Generic CRUD table for an estate sub-resource (fiduciaries, beneficiaries,
 * assets, liabilities, distributions, deadlines, accounting).
 *
 * Props:
 *   estateId   — parent estate id
 *   resource   — API resource segment
 *   title      — heading
 *   columns    — [{ key, label, render?(value,row) }]
 *   fields     — [{ key, label, type, options?, required?, half? }]
 *   emptyText  — empty-state copy
 *   onChanged  — optional callback fired after any mutation (to refresh parent)
 *   headerSlot — optional node rendered above the table (e.g. accounting summary)
 */
const inputCls = 'w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface'
const labelCls = 'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1'

function fmtCell(col, row) {
  const value = row[col.key]
  if (col.render) return col.render(value, row)
  if (value === null || value === undefined || value === '') return <span className="text-brand-line-2">—</span>
  return value
}

function emptyForm(fields) {
  const f = {}
  fields.forEach((fld) => { f[fld.key] = fld.type === 'checkbox' ? false : '' })
  return f
}

export function fmtMoney(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  if (Number.isNaN(n)) return v
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export function fmtDate(v) {
  if (!v) return null
  try { return format(parseISO(v), 'MMM d, yyyy') } catch { return v }
}

export default function EstateSubTable({ estateId, resource, title, columns, fields, emptyText, onChanged, headerSlot }) {
  const confirmAction = useConfirm()
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(emptyForm(fields))
  const [editingId, setEditingId] = useState(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    listEstateChildren(estateId, resource)
      .then((data) => setRows(Array.isArray(data) ? data : []))
      .catch(() => setError('Failed to load.'))
      .finally(() => setLoading(false))
  }, [estateId, resource])

  useEffect(() => { load() }, [load])

  const buildPayload = (f) => {
    const payload = {}
    fields.forEach((fld) => {
      let v = f[fld.key]
      if (v === '' ) { v = null }
      if (fld.type === 'number' && v !== null) v = Number(v)
      if (fld.type === 'checkbox') v = !!v
      payload[fld.key] = v
    })
    return payload
  }

  const handleCreate = async () => {
    const required = fields.filter((fld) => fld.required)
    if (required.some((fld) => !form[fld.key])) return
    setSaving(true)
    try {
      await createEstateChild(estateId, resource, buildPayload(form))
      setForm(emptyForm(fields))
      setAdding(false)
      load()
      onChanged && onChanged()
    } catch { setError('Failed to save.') } finally { setSaving(false) }
  }

  const startEdit = (row) => {
    const f = emptyForm(fields)
    fields.forEach((fld) => { f[fld.key] = row[fld.key] ?? (fld.type === 'checkbox' ? false : '') })
    setForm(f)
    setEditingId(row.id)
    setAdding(false)
  }

  const handleUpdate = async () => {
    setSaving(true)
    try {
      await updateEstateChild(estateId, resource, editingId, buildPayload(form))
      setEditingId(null)
      setForm(emptyForm(fields))
      load()
      onChanged && onChanged()
    } catch { setError('Failed to save.') } finally { setSaving(false) }
  }

  const handleDelete = async (id) => {
    if (!await confirmAction({ title: 'Delete entry?', message: 'This entry will be permanently removed.', confirmLabel: 'Delete entry', destructive: true })) return
    try {
      await deleteEstateChild(estateId, resource, id)
      setRows((prev) => prev.filter((r) => r.id !== id))
      onChanged && onChanged()
    } catch { setError('Failed to delete.') }
  }

  const renderFormFields = () => (
    <div className="grid grid-cols-2 gap-4">
      {fields.map((fld) => (
        <div key={fld.key} className={fld.half ? '' : 'col-span-2'}>
          <label className={labelCls}>{fld.label}{fld.required && ' *'}</label>
          {fld.type === 'select' ? (
            <select value={form[fld.key] ?? ''} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.value }))} className={inputCls}>
              <option value="">—</option>
              {fld.options.map((o) => {
                const val = typeof o === 'string' ? o : o.value
                const lbl = typeof o === 'string' ? o.replace(/_/g, ' ') : o.label
                return <option key={val} value={val}>{lbl}</option>
              })}
            </select>
          ) : fld.type === 'textarea' ? (
            <textarea rows={2} value={form[fld.key] ?? ''} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.value }))} className={`${inputCls} resize-none`} />
          ) : fld.type === 'checkbox' ? (
            <input type="checkbox" checked={!!form[fld.key]} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.checked }))} className="mt-2 h-4 w-4 accent-brand-accent" />
          ) : (
            <input type={fld.type === 'number' ? 'number' : fld.type === 'date' ? 'date' : 'text'} value={form[fld.key] ?? ''} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.value }))} className={inputCls} />
          )}
        </div>
      ))}
    </div>
  )

  return (
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
        <h2 className="font-serif font-bold text-xl text-brand-ink">{title}</h2>
        {!adding && editingId === null && (
          <button onClick={() => { setAdding(true); setForm(emptyForm(fields)) }} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm">
            <Plus size={16} /> Add
          </button>
        )}
      </div>

      {headerSlot}

      {(adding || editingId !== null) && (
        <div className="p-6 bg-brand-bg border-b border-brand-line">
          <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">{editingId ? 'Edit Entry' : 'New Entry'}</h3>
          {renderFormFields()}
          {error && <p className="text-brand-rose text-sm font-sans mt-3">{error}</p>}
          <div className="flex gap-3 justify-end mt-5">
            <button onClick={() => { setAdding(false); setEditingId(null); setForm(emptyForm(fields)) }} className="px-4 py-2 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink flex items-center gap-1.5"><X size={15} /> Cancel</button>
            <button onClick={editingId ? handleUpdate : handleCreate} disabled={saving} className="px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm flex items-center gap-1.5">
              <Check size={15} /> {saving ? 'Saving…' : editingId ? 'Update' : 'Save'}
            </button>
          </div>
        </div>
      )}

      <div className="p-2">
        {loading ? (
          <div className="flex justify-center py-12"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
        ) : rows.length === 0 ? (
          <div className="text-center py-12 px-6">
            <p className="text-brand-ink-2 font-sans text-sm">{emptyText || 'No entries yet.'}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left">
              <thead>
                <tr className="border-b border-brand-line">
                  {columns.map((c) => (
                    <th key={c.key} className="px-4 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap">{c.label}</th>
                  ))}
                  <th className="px-4 py-3 w-20"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-brand-line">
                {rows.map((row) => (
                  <tr key={row.id} className="hover:bg-brand-bg-soft transition-colors group">
                    {columns.map((c) => (
                      <td key={c.key} className="px-4 py-3 text-[13px] font-sans text-brand-ink-2 whitespace-nowrap">{fmtCell(c, row)}</td>
                    ))}
                    <td className="px-4 py-3 text-right">
                      <div className="flex gap-1.5 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                        <button onClick={() => startEdit(row)} className="text-brand-muted hover:text-brand-ink p-1"><Pencil size={15} /></button>
                        <button onClick={() => handleDelete(row.id)} className="text-brand-muted hover:text-brand-rose p-1"><Trash2 size={15} /></button>
                      </div>
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
