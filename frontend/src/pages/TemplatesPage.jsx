import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  getTemplates,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
  getMattersV2,
  discoverTemplateVariables,
} from '../api'
import {
  FileText,
  Plus,
  Pencil,
  Trash2,
  X,
  Send,
  Eye,
  Sparkles,
  Check,
  ClipboardList,
  PenLine,
  CheckCircle2,
  Palette,
  Clock3,
  AlertCircle,
  Search,
  Wand2,
  FileCheck2,
} from 'lucide-react'

const CATEGORY_COLORS = {
  engagement_letter: 'bg-blue-100 text-blue-800',
  retainer: 'bg-purple-100 text-purple-800',
  NDA: 'bg-amber-100 text-amber-800',
  motion: 'bg-rose-100 text-rose-800',
  other: 'bg-gray-100 text-gray-800',
}

const CATEGORY_LABELS = {
  engagement_letter: 'Engagement Letter',
  retainer: 'Retainer',
  NDA: 'NDA',
  motion: 'Motion',
  other: 'Other',
}

const TABS = [
  { key: 'templates', label: 'Templates', icon: FileText },
  { key: 'generate', label: 'Generate / Smart Fill', icon: Wand2 },
  { key: 'esign', label: 'E-Sign Queue', icon: PenLine },
  { key: 'approvals', label: 'Approvals', icon: CheckCircle2 },
  { key: 'branding', label: 'Branding / Settings', icon: Palette },
]

const normalizeItems = (data) => (Array.isArray(data) ? data : (data?.items || []))

const getErrorMessage = (err, fallback) => (
  err?.response?.data?.detail || err?.message || fallback
)

const getTemplateVariables = (template) => {
  const matches = template?.body?.match(/\{\{(.+?)\}\}/g) || []
  return [...new Set(matches.map((m) => m.slice(2, -2).trim()).filter(Boolean))]
}

const friendlyVariableLabel = (name) => name
  .replace(/[_-]+/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase())

const formatMatterLabel = (matter) => {
  if (!matter) return ''
  const name = matter.matter_name || matter.title || matter.name || 'Untitled matter'
  return [name, matter.client_name, matter.practice_area, matter.status].filter(Boolean).join(' - ')
}

function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-12">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-brand-surface-2 border border-brand-line rounded-lg shadow-xl w-full max-w-3xl mx-4 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-brand-line">
          <h2 className="text-lg font-semibold text-brand-ink">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 text-brand-muted hover:text-brand-ink"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

function ConfirmDialog({ message, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onCancel} />
      <div className="relative bg-brand-surface-2 border border-brand-line rounded-lg shadow-xl p-6 max-w-sm w-full mx-4">
        <p className="text-brand-ink mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm text-white bg-brand-rose hover:bg-red-700 rounded"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

function TemplateForm({ initial, onSubmit, onCancel }) {
  const [title, setTitle] = useState(initial?.title || '')
  const [body, setBody] = useState(initial?.body || '')
  const [category, setCategory] = useState(initial?.category || 'other')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!title.trim() || !body.trim()) return
    setSaving(true)
    setError(null)
    try {
      await onSubmit({ title: title.trim(), body: body.trim(), category })
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to save template.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
          {error}
        </div>
      )}
      <div>
        <label className="block text-sm font-medium text-brand-ink mb-1">
          Title
        </label>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder="Engagement Letter Template"
          required
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-brand-ink mb-1">
          Category
        </label>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
        >
          {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-brand-ink mb-1">
          Body
        </label>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono"
          rows={16}
          placeholder={'Dear {{client_name}},\n\nThis letter confirms...\n\nSincerely,\n{{attorney_name}}'}
          required
        />
        <p className="text-xs text-brand-muted mt-1">
          Use {'{{variable_name}}'} for placeholders.
        </p>
      </div>
      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={saving}
          className="px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
        >
          {saving ? 'Saving...' : initial ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  )
}

function MatterPicker({ matters, selectedMatterId, onSelect, loading }) {
  const [query, setQuery] = useState('')
  const selected = matters.find((matter) => matter.id === selectedMatterId)
  const filtered = matters.filter((matter) => {
    const q = query.trim().toLowerCase()
    if (!q) return true
    return (
      matter.matter_name?.toLowerCase().includes(q) ||
      matter.client_name?.toLowerCase().includes(q) ||
      matter.practice_area?.toLowerCase().includes(q) ||
      matter.id?.toLowerCase().includes(q)
    )
  }).slice(0, 8)

  return (
    <div className="border border-brand-line rounded bg-brand-bg p-3">
      <label className="block text-sm font-medium text-brand-ink mb-2">
        Matter
      </label>
      <div className="relative">
        <Search size={15} className="absolute left-3 top-2.5 text-brand-muted" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full pl-9 pr-3 py-2 border border-brand-line rounded text-sm bg-brand-surface-2 text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder={loading ? 'Loading matters...' : 'Search by matter, client, or practice area'}
        />
      </div>
      {selected && (
        <div className="mt-2 flex items-center justify-between gap-2 text-xs bg-brand-surface-2 border border-brand-line rounded px-3 py-2">
          <span className="text-brand-ink truncate">{formatMatterLabel(selected)}</span>
          <button
            type="button"
            onClick={() => onSelect('')}
            className="text-brand-muted hover:text-brand-ink"
          >
            Clear
          </button>
        </div>
      )}
      <div className="mt-2 max-h-48 overflow-y-auto space-y-1">
        {filtered.map((matter) => (
          <button
            key={matter.id}
            type="button"
            onClick={() => onSelect(matter.id)}
            className={`w-full text-left px-3 py-2 rounded border text-sm transition-colors ${
              selectedMatterId === matter.id
                ? 'border-brand-accent bg-brand-accent/10 text-brand-ink'
                : 'border-transparent hover:border-brand-line hover:bg-brand-surface-2 text-brand-ink'
            }`}
          >
            <span className="block font-medium truncate">{matter.matter_name || 'Untitled matter'}</span>
            <span className="block text-xs text-brand-muted truncate">
              {[matter.client_name, matter.practice_area, matter.status].filter(Boolean).join(' - ') || matter.id}
            </span>
          </button>
        ))}
        {!loading && filtered.length === 0 && (
          <p className="text-xs text-brand-muted px-1 py-2">
            No matching matters. Paste a matter UUID below if needed.
          </p>
        )}
      </div>
    </div>
  )
}

function RenderModal({ template, matters, matterLoading, onClose }) {
  const [variables, setVariables] = useState({})
  const [matterId, setMatterId] = useState('')
  const [rendered, setRendered] = useState(null)
  const [matterDocId, setMatterDocId] = useState(null)
  const [rendering, setRendering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [smartFillState, setSmartFillState] = useState('idle')
  const [smartFillMessage, setSmartFillMessage] = useState('')

  const names = useMemo(() => getTemplateVariables(template), [template])

  useEffect(() => {
    const initialVars = {}
    names.forEach((name) => {
      initialVars[name] = ''
    })
    setVariables(initialVars)
    setSaved(false)
    setRendered(null)
    setMatterDocId(null)
  }, [names])

  const setVariable = (name, value) => {
    setSaved(false)
    setVariables((prev) => ({ ...prev, [name]: value }))
  }

  const normalizeDiscovery = (res) => {
    const values = res?.variables || res?.values || res?.field_values || {}
    const next = {}
    if (Array.isArray(values)) {
      values.forEach((item) => {
        const key = item?.variable || item?.name || item?.key
        if (!key) return
        next[key] = item?.suggested_value ?? item?.value ?? item?.text ?? ''
      })
      return next
    }
    Object.entries(values).forEach(([key, value]) => {
      if (value && typeof value === 'object') {
        next[key] = value.suggested_value ?? value.value ?? value.text ?? ''
      } else {
        next[key] = value ?? ''
      }
    })
    return next
  }

  const handleSmartFill = async () => {
    if (!matterId.trim()) {
      setSmartFillState('error')
      setSmartFillMessage('Choose a matter before smart fill.')
      return
    }
    setSmartFillState('loading')
    setSmartFillMessage('')
    try {
      const res = await discoverTemplateVariables(template.id, {
        matter_id: matterId.trim(),
        variables: names,
      })
      const discovered = normalizeDiscovery(res)
      if (Object.keys(discovered).length === 0) {
        setSmartFillState('empty')
        setSmartFillMessage('No smart-fill values were returned for this template yet.')
        return
      }
      setVariables((prev) => ({ ...prev, ...discovered }))
      setSaved(false)
      setSmartFillState('ready')
      setSmartFillMessage('Smart-fill values loaded. Review each field before saving.')
    } catch (err) {
      if ([404, 405, 501].includes(err?.response?.status)) {
        setSmartFillState('unavailable')
        setSmartFillMessage('Smart fill is not enabled on this server yet. Manual fields are ready for review.')
      } else {
        setSmartFillState('error')
        setSmartFillMessage(getErrorMessage(err, 'Smart fill failed.'))
      }
    }
  }

  const handleRender = async () => {
    setRendering(true)
    setError(null)
    try {
      const res = await renderTemplate(template.id, {
        variables,
        matter_id: matterId.trim() || null,
      })
      setRendered(res.rendered)
      if (res.matter_document_id) {
        setMatterDocId(res.matter_document_id)
      }
    } catch (err) {
      setError(getErrorMessage(err, 'Render failed.'))
    } finally {
      setRendering(false)
    }
  }

  const handleSave = async () => {
    if (!matterId.trim()) return
    setSaving(true)
    setError(null)
    try {
      const res = await renderTemplate(template.id, {
        variables,
        matter_id: matterId.trim(),
      })
      setRendered(res.rendered)
      if (res.matter_document_id) {
        setMatterDocId(res.matter_document_id)
      }
      setSaved(true)
    } catch (err) {
      setError(getErrorMessage(err, 'Save failed.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={`Generate Document: ${template.title}`} onClose={onClose}>
      <div className="space-y-4">
        {error && (
          <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
            {error}
          </div>
        )}

        <MatterPicker
          matters={matters}
          selectedMatterId={matterId}
          onSelect={(id) => { setMatterId(id); setSaved(false) }}
          loading={matterLoading}
        />

        <div>
          <label className="block text-xs font-medium text-brand-muted mb-0.5">
            Matter UUID fallback
          </label>
          <input
            type="text"
            value={matterId}
            onChange={(e) => { setMatterId(e.target.value); setSaved(false) }}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono"
            placeholder="Paste matter UUID if the matter is not listed"
          />
        </div>

        {names.length > 0 && (
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border border-brand-line rounded bg-brand-bg px-3 py-2">
            <div>
              <p className="text-sm font-medium text-brand-ink">Smart fill</p>
              <p className="text-xs text-brand-muted">
                Pull matter-aware values when the backend endpoint is available.
              </p>
            </div>
            <button
              onClick={handleSmartFill}
              disabled={smartFillState === 'loading' || !matterId.trim()}
              className="flex items-center justify-center gap-2 px-3 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-surface-2 disabled:opacity-50"
            >
              <Wand2 size={15} />
              {smartFillState === 'loading' ? 'Filling...' : 'Smart Fill'}
            </button>
          </div>
        )}

        {smartFillMessage && (
          <div className={`text-sm border px-3 py-2 ${
            smartFillState === 'ready'
              ? 'text-brand-green bg-brand-green/10 border-brand-green/30'
              : smartFillState === 'error'
                ? 'text-brand-rose bg-brand-rose/10 border-brand-rose/30'
                : 'text-brand-muted bg-brand-bg border-brand-line'
          }`}>
            {smartFillMessage}
          </div>
        )}

        {names.length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-brand-ink mb-2">
              Fields To Review
            </h3>
            <div className="space-y-2">
              {names.map((name) => (
                <div key={name}>
                  <label className="block text-xs font-medium text-brand-muted mb-0.5">
                    {friendlyVariableLabel(name)}
                    <span className="font-mono text-brand-muted ml-2">
                      {'{{'}{name}{'}}'}
                    </span>
                  </label>
                  <input
                    type="text"
                    value={variables[name] || ''}
                    onChange={(e) => setVariable(name, e.target.value)}
                    className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                    placeholder={`Enter ${friendlyVariableLabel(name)}`}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {names.length === 0 && (
          <p className="text-sm text-brand-muted italic">
            This template has no variables. Preview or save it directly.
          </p>
        )}

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={handleRender}
            disabled={rendering}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
          >
            <Eye size={16} />
            {rendering ? 'Rendering...' : 'Preview'}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || saved || !matterId.trim()}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-accent hover:opacity-90 rounded disabled:opacity-50"
          >
            {saved ? (
              <>
                <Check size={16} /> Saved
              </>
            ) : (
              <>
                <Send size={16} />
                {saving ? 'Saving...' : 'Render & Save to Matter'}
              </>
            )}
          </button>
        </div>

        {rendered && (
          <div>
            <h3 className="text-sm font-medium text-brand-ink mb-2">
              Rendered Output
            </h3>
            <div className="bg-brand-bg border border-brand-line rounded p-4 whitespace-pre-wrap font-mono text-sm text-brand-ink max-h-96 overflow-y-auto">
              {rendered}
            </div>
            {matterDocId && (
              <p className="text-xs text-brand-green mt-1">
                Saved as matter document: {matterDocId}
              </p>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

function ComingSoonPanel({ icon: Icon, title, description, items }) {
  return (
    <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-6">
      <div className="flex items-start gap-4">
        <div className="p-2 rounded bg-brand-bg text-brand-accent">
          <Icon size={22} />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-brand-ink">{title}</h2>
          <p className="text-sm text-brand-muted mt-1 max-w-3xl">
            {description}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-6">
        {items.map((item) => (
          <div key={item.title} className="border border-brand-line rounded p-4 bg-brand-bg">
            <p className="text-sm font-medium text-brand-ink">{item.title}</p>
            <p className="text-xs text-brand-muted mt-1">{item.body}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TemplatesPage() {
  const [templates, setTemplates] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [matterLoading, setMatterLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('templates')
  const [showCreate, setShowCreate] = useState(false)
  const [editTemplate, setEditTemplate] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [renderTarget, setRenderTarget] = useState(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const res = await getTemplates({ include_inactive: true })
      setTemplates(res.items || [])
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load templates.'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMatters = useCallback(async () => {
    setMatterLoading(true)
    try {
      const res = await getMattersV2({
        page_size: 100,
        sort_by: 'updated_at',
        sort_dir: 'desc',
      })
      setMatters(normalizeItems(res))
    } catch {
      setMatters([])
    } finally {
      setMatterLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    loadMatters()
  }, [load, loadMatters])

  const handleCreate = async (data) => {
    await createTemplate(data)
    setShowCreate(false)
    await load()
  }

  const handleUpdate = async (data) => {
    await updateTemplate(editTemplate.id, data)
    setEditTemplate(null)
    await load()
  }

  const handleDelete = async () => {
    try {
      await deleteTemplate(deleteTarget.id)
      setDeleteTarget(null)
      await load()
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to delete template.'))
    }
  }

  const toggleActive = async (tpl) => {
    try {
      await updateTemplate(tpl.id, { is_active: !tpl.is_active })
      await load()
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to toggle template status.'))
    }
  }

  const bodyPreview = (body) => (
    body.length > 100 ? `${body.slice(0, 100)}...` : body
  )

  const activeTemplates = templates.filter((tpl) => tpl.is_active).length
  const variableCount = templates.reduce((sum, tpl) => sum + getTemplateVariables(tpl).length, 0)
  const selectedTemplate = templates.find((tpl) => tpl.is_active) || templates[0] || null

  const renderTemplatesPanel = () => {
    if (templates.length === 0) {
      return (
        <div className="text-center py-16 bg-brand-surface-2 border border-brand-line rounded-lg">
          <FileText
            size={48}
            className="mx-auto text-brand-muted mb-4 opacity-30"
          />
          <p className="text-brand-muted">
            No templates yet. Create the first automation template.
          </p>
        </div>
      )
    }

    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {templates.map((tpl) => {
          const vars = getTemplateVariables(tpl)
          return (
            <div
              key={tpl.id}
              className="bg-brand-surface-2 border border-brand-line rounded-lg shadow-sm hover:shadow-md transition-shadow"
            >
              <div className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-medium text-brand-ink truncate">
                    {tpl.title}
                  </h3>
                  <span
                    className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full font-semibold shrink-0 ml-2 ${
                      CATEGORY_COLORS[tpl.category] || CATEGORY_COLORS.other
                    }`}
                  >
                    {CATEGORY_LABELS[tpl.category] || tpl.category}
                  </span>
                </div>
                <p className="text-xs text-brand-muted font-mono mb-3 min-h-10">
                  {bodyPreview(tpl.body)}
                </p>

                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <span className="inline-flex items-center gap-1 text-xs text-brand-muted border border-brand-line rounded px-2 py-1">
                    <ClipboardList size={12} />
                    {vars.length} field{vars.length === 1 ? '' : 's'}
                  </span>
                  <button
                    onClick={() => toggleActive(tpl)}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      tpl.is_active ? 'bg-brand-green' : 'bg-brand-muted/30'
                    }`}
                    aria-label={tpl.is_active ? 'Deactivate template' : 'Activate template'}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                        tpl.is_active ? 'translate-x-4' : 'translate-x-1'
                      }`}
                    />
                  </button>
                  <span className="text-xs text-brand-muted">
                    {tpl.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>

                <div className="flex items-center gap-2 pt-2 border-t border-brand-line">
                  <button
                    onClick={() => setEditTemplate(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-muted hover:text-brand-ink border border-brand-line rounded"
                  >
                    <Pencil size={14} />
                    Edit
                  </button>
                  <button
                    onClick={() => setRenderTarget(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-muted hover:text-brand-ink border border-brand-line rounded"
                  >
                    <Sparkles size={14} />
                    Generate
                  </button>
                  <button
                    onClick={() => setDeleteTarget(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-rose hover:text-red-800 border border-brand-line rounded ml-auto"
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  const renderGeneratePanel = () => (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-5">
      <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-5">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-brand-ink">Generate / Smart Fill</h2>
            <p className="text-sm text-brand-muted mt-1">
              Start from an active template, choose a matter, review fields, preview, then save the generated document to the matter.
            </p>
          </div>
          <button
            onClick={() => selectedTemplate && setRenderTarget(selectedTemplate)}
            disabled={!selectedTemplate}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
          >
            <Sparkles size={16} />
            Generate
          </button>
        </div>

        <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-3">
          {templates.slice(0, 6).map((tpl) => {
            const vars = getTemplateVariables(tpl)
            return (
              <button
                key={tpl.id}
                onClick={() => setRenderTarget(tpl)}
                className="text-left border border-brand-line rounded p-4 bg-brand-bg hover:border-brand-accent/50"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-brand-ink truncate">{tpl.title}</p>
                    <p className="text-xs text-brand-muted mt-1">
                      {vars.length} review field{vars.length === 1 ? '' : 's'}
                    </p>
                  </div>
                  <span className={`text-[10px] uppercase px-2 py-0.5 rounded-full font-semibold shrink-0 ${
                    tpl.is_active ? 'bg-brand-green/10 text-brand-green' : 'bg-brand-muted/10 text-brand-muted'
                  }`}>
                    {tpl.is_active ? 'Active' : 'Draft'}
                  </span>
                </div>
              </button>
            )
          })}
          {templates.length === 0 && (
            <div className="border border-dashed border-brand-line rounded p-6 text-center md:col-span-2">
              <p className="text-sm text-brand-muted">Create a template before generating documents.</p>
            </div>
          )}
        </div>
      </div>

      <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-5">
        <h3 className="text-sm font-semibold text-brand-ink">Integration Hooks</h3>
        <div className="mt-4 space-y-3">
          <div className="flex gap-3">
            <Check size={16} className="text-brand-green shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Existing render endpoint remains the source of truth.</p>
          </div>
          <div className="flex gap-3">
            <Clock3 size={16} className="text-brand-amber shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Optional smart-fill discovery calls are attempted only from the generate modal.</p>
          </div>
          <div className="flex gap-3">
            <AlertCircle size={16} className="text-brand-muted shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">If discovery is unavailable, users keep the manual review fields.</p>
          </div>
        </div>
      </div>
    </div>
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-brand-bg">
        <div className="text-brand-muted">Loading document automation...</div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto bg-brand-bg p-4 md:p-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 mb-5">
        <div>
          <h1 className="text-xl font-semibold text-brand-ink">
            Document Automation
          </h1>
          <p className="text-sm text-brand-muted mt-1">
            Build templates, generate matter-ready documents, and prepare them for approvals and e-signature.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded"
        >
          <Plus size={18} />
          New Template
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Templates</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{templates.length}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Active</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{activeTemplates}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Mapped Fields</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{variableCount}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Recent Matters</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{matters.length}</p>
        </div>
      </div>

      <div className="mb-5 overflow-x-auto border-b border-brand-line">
        <div className="flex min-w-max gap-1">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm border-b-2 transition-colors ${
                activeTab === key
                  ? 'border-brand-accent text-brand-ink'
                  : 'border-transparent text-brand-muted hover:text-brand-ink'
              }`}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-4 text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-4 py-3">
          {error}
        </div>
      )}

      {activeTab === 'templates' && renderTemplatesPanel()}
      {activeTab === 'generate' && renderGeneratePanel()}
      {activeTab === 'esign' && (
        <ComingSoonPanel
          icon={PenLine}
          title="E-Sign Queue"
          description="The matter-level signature workflow remains available today. This workspace is ready to centralize generated documents, signer roles, reminders, voids, and completed audit certificates."
          items={[
            { title: 'Ready to send', body: 'Generated matter documents can be queued here once the cross-matter endpoint lands.' },
            { title: 'Signer status', body: 'Track sent, viewed, signed, declined, expired, and voided states.' },
            { title: 'Executed copies', body: 'Surface signed documents and audit certificates saved back to matters.' },
          ]}
        />
      )}
      {activeTab === 'approvals' && (
        <ComingSoonPanel
          icon={FileCheck2}
          title="Approvals"
          description="Template lifecycle and attorney review states can plug into this panel without changing the existing template editor."
          items={[
            { title: 'Draft review', body: 'Submit templates for test render and activation approval.' },
            { title: 'Generated output', body: 'Route completed drafts for attorney approval before signing or filing.' },
            { title: 'Version history', body: 'Prepare for immutable versions, compare, clone, and rollback.' },
          ]}
        />
      )}
      {activeTab === 'branding' && (
        <ComingSoonPanel
          icon={Palette}
          title="Branding / Settings"
          description="Tenant branding hooks are framed here for letterhead, packet covers, e-sign emails, and portal delivery notices."
          items={[
            { title: 'Letterhead', body: 'Logos, colors, firm address, and disclaimer fields.' },
            { title: 'Packet defaults', body: 'Cover-page settings, output naming, and document visibility.' },
            { title: 'Provider settings', body: 'Dropbox Sign and DocuSign credential checks can fail closed here.' },
          ]}
        />
      )}

      {showCreate && (
        <Modal title="Create Template" onClose={() => setShowCreate(false)}>
          <TemplateForm
            onSubmit={handleCreate}
            onCancel={() => setShowCreate(false)}
          />
        </Modal>
      )}

      {editTemplate && (
        <Modal title="Edit Template" onClose={() => setEditTemplate(null)}>
          <TemplateForm
            initial={editTemplate}
            onSubmit={handleUpdate}
            onCancel={() => setEditTemplate(null)}
          />
        </Modal>
      )}

      {deleteTarget && (
        <ConfirmDialog
          message={`Delete "${deleteTarget.title}"? This cannot be undone.`}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {renderTarget && (
        <RenderModal
          template={renderTarget}
          matters={matters}
          matterLoading={matterLoading}
          onClose={() => setRenderTarget(null)}
        />
      )}
    </div>
  )
}
