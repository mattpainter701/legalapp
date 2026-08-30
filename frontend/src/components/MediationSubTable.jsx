import React, { useState, useEffect, useCallback } from 'react'
import { format, parseISO } from 'date-fns'
import { Plus, Trash2, Pencil, Check, Upload } from 'lucide-react'
import { useConfirm } from './dialog/ConfirmProvider'

/**
 * Generic CRUD table for mediation sub-resources (parties, assets, proposals).
 *
 * Props:
 *   caseId     — parent mediation case id
 *   title      — heading
 *   columns    — [{ key, label, render?(value,row) }]
 *   fields     — [{ key, label, type, options?, required?, half?, full?, placeholder? }]
 *   emptyText  — empty-state copy
 *   listFn     — () => Promise<rows[]>
 *   createFn   — (payload) => Promise<row> | null to hide add button
 *   updateFn   — (rowId, payload) => Promise<row> | null to hide edit
 *   deleteFn   — (rowId) => Promise<void> | null to hide delete
 *   updateCondition/deleteCondition — optional row predicates for immutable states
 *   actions    — optional [{ label, icon, onClick, condition? }]
 *   onChanged  — optional callback after any mutation
 *   headerSlot — optional node rendered above the table
 *   uploadFn   — optional (file, description) => Promise<row>
 */

const inputCls = 'w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface'
const labelCls = 'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1'

function fmtCell(col, row) {
  const value = row[col.key]
  if (col.render) return col.render(value, row)
  if (value === null || value === undefined || value === '') return <span className="text-brand-line-2">—</span>
  if (typeof value === 'boolean') return value ? <Check size={15} className="text-brand-green" /> : <span className="text-brand-line-2">—</span>
  if (col.key.includes('date') || col.key.includes('_at')) {
    try { return format(parseISO(value), 'MMM d, yyyy') } catch { return value }
  }
  return String(value)
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

export default function MediationSubTable({
  caseId, title, columns, fields, emptyText,
  listFn, createFn, updateFn, deleteFn,
  updateCondition, deleteCondition,
  actions, onChanged, headerSlot, uploadFn,
}) {
  const formId = React.useId()
  const confirmAction = useConfirm()
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(emptyForm(fields))
  const [editingId, setEditingId] = useState(null)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadDesc, setUploadDesc] = useState('')
  const fileRef = React.useRef(null)

  const load = useCallback(() => {
    setLoading(true)
    listFn()
      .then((data) => setRows(Array.isArray(data) ? data : []))
      .catch(() => setError('Failed to load.'))
      .finally(() => setLoading(false))
  }, [listFn])

  useEffect(() => { load() }, [load])

  const buildPayload = (f) => {
    const payload = {}
    fields.forEach((fld) => {
      let v = f[fld.key]
      if (v === '') { v = null }
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
      await createFn(buildPayload(form))
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
      await updateFn(editingId, buildPayload(form))
      setEditingId(null)
      load()
      onChanged && onChanged()
    } catch { setError('Failed to save.') } finally { setSaving(false) }
  }

  const handleDelete = async (rowId) => {
    if (!await confirmAction({ title: 'Delete entry?', message: 'This entry will be permanently removed.', confirmLabel: 'Delete entry', destructive: true })) return
    try {
      await deleteFn(rowId)
      load()
      onChanged && onChanged()
    } catch { setError('Failed to delete.') }
  }

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file || !uploadFn) return
    setUploading(true)
    try {
      await uploadFn(file, uploadDesc || undefined)
      setUploadDesc('')
      if (fileRef.current) fileRef.current.value = ''
      load()
      onChanged && onChanged()
    } catch { setError('Upload failed.') } finally { setUploading(false) }
  }

  const editingRow = editingId ? rows.find((r) => r.id === editingId) : null

  return (
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
        <h2 className="font-serif font-bold text-xl text-brand-ink">{title}</h2>
        <div className="flex items-center gap-3">
          {uploadFn && (
            <div className="flex items-center gap-2">
              <input type="text" value={uploadDesc} onChange={(e) => setUploadDesc(e.target.value)} placeholder="Description (optional)" className={`${inputCls} w-48`} />
              <input type="file" ref={fileRef} className="hidden" />
              <button onClick={() => fileRef.current?.click()} className="flex items-center gap-1.5 px-3 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink transition-colors">
                <Upload size={14} /> Choose
              </button>
              <button onClick={handleUpload} disabled={uploading || !fileRef.current?.files?.[0]} className="flex items-center gap-1.5 px-3 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-colors">
                {uploading ? 'Uploading…' : 'Upload'}
              </button>
            </div>
          )}
          {createFn && (
            <button onClick={() => { setAdding((v) => !v); setEditingId(null); setForm(emptyForm(fields)) }} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm">
              <Plus size={16} /> Add
            </button>
          )}
        </div>
      </div>

      {headerSlot}

      {(adding || editingId) && (
        <div className="p-6 bg-brand-bg border-b border-brand-line">
          <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">
            {editingId ? `Editing: ${editingRow?.name || editingRow?.description || editingRow?.title || ''}` : `New ${title.slice(-1) === 's' ? title.slice(0, -1) : title}`}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
            {fields.map((fld) => (
              <div key={fld.key} className={fld.full ? 'md:col-span-2' : ''}>
                <label htmlFor={`${formId}-${fld.key}`} className={labelCls}>{fld.label}</label>
                {fld.type === 'select' ? (
                  <select id={`${formId}-${fld.key}`} value={form[fld.key] ?? ''} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.value }))} className={inputCls}>
                    <option value="">—</option>
                    {(fld.options || []).map((o) => {
                      const val = typeof o === 'string' ? o : o.value
                      const lbl = typeof o === 'string' ? o.charAt(0).toUpperCase() + o.slice(1).replace(/_/g, ' ') : o.label
                      return <option key={val} value={val}>{lbl}</option>
                    })}
                  </select>
                ) : fld.type === 'textarea' ? (
                  <textarea id={`${formId}-${fld.key}`} value={form[fld.key] ?? ''} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.value }))} rows={3} className={`${inputCls} resize-none`} />
                ) : fld.type === 'checkbox' ? (
                  <label className="flex items-center gap-2 cursor-pointer pt-1">
                    <input id={`${formId}-${fld.key}`} type="checkbox" checked={!!form[fld.key]} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.checked }))} className="w-4 h-4 rounded border-brand-line text-brand-ink focus:ring-brand-accent" />
                    <span className="text-[13px] font-sans text-brand-ink-2">{fld.label}</span>
                  </label>
                ) : (
                  <input id={`${formId}-${fld.key}`} type={fld.type || 'text'} value={form[fld.key] ?? ''} onChange={(e) => setForm((p) => ({ ...p, [fld.key]: e.target.value }))} className={inputCls} placeholder={fld.placeholder} />
                )}
              </div>
            ))}
          </div>
          {error && <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{error}</p>}
          <div className="flex gap-3 justify-end">
            <button onClick={() => { setAdding(false); setEditingId(null); setError(null) }} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors">Cancel</button>
            <button onClick={editingId ? handleUpdate : handleCreate} disabled={saving} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm">
              {saving ? 'Saving…' : editingId ? 'Save Changes' : 'Create'}
            </button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        {loading ? (
          <div className="flex justify-center py-16"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
        ) : rows.length === 0 ? (
          <div className="text-center py-16 px-6">
            <p className="text-brand-ink font-serif text-lg font-bold mb-1">No entries yet</p>
            <p className="text-brand-muted text-sm font-sans">{emptyText}</p>
          </div>
        ) : (
          <table className="min-w-full text-left border-collapse">
            <thead>
              <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                {columns.map((col) => (
                  <th key={col.key} className="px-5 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap">{col.label}</th>
                ))}
                {(actions?.length > 0 || updateFn || deleteFn) && (
                  <th className="px-5 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans text-right">Actions</th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-brand-line">
              {rows.map((row) => (
                <tr key={row.id} className="hover:bg-brand-bg-soft transition-colors">
                  {columns.map((col) => (
                    <td key={col.key} className="px-5 py-3 text-[13px] font-sans text-brand-ink-2 whitespace-nowrap">{fmtCell(col, row)}</td>
                  ))}
                  {(actions?.length > 0 || updateFn || deleteFn) && (
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {actions?.filter((a) => !a.condition || a.condition(row)).map((a, i) => {
                          const Icon = a.icon
                          return <button key={i} onClick={() => a.onClick(row)} className="inline-flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-sans font-semibold uppercase tracking-wide rounded-md border transition-colors hover:bg-brand-bg-soft" title={a.label}><Icon size={13} /> {a.label}</button>
                        })}
                        {updateFn && (!updateCondition || updateCondition(row)) && <button onClick={() => startEdit(row)} className="p-1.5 text-brand-ink-2 hover:text-brand-ink transition-colors rounded" title="Edit"><Pencil size={14} /></button>}
                        {deleteFn && (!deleteCondition || deleteCondition(row)) && <button onClick={() => handleDelete(row.id)} className="p-1.5 text-brand-ink-2 hover:text-brand-rose transition-colors rounded" title="Delete"><Trash2 size={14} /></button>}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
