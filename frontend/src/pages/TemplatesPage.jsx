import React, { useState, useEffect, useCallback } from 'react'
import {
  getTemplates,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
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

function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-12">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white rounded-lg shadow-xl w-full max-w-2xl mx-4 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600"
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
      <div className="relative bg-white rounded-lg shadow-xl p-6 max-w-sm w-full mx-4">
        <p className="text-gray-900 mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 border border-gray-300 rounded"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm text-white bg-red-600 hover:bg-red-700 rounded"
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

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!title.trim() || !body.trim()) return
    setSaving(true)
    try {
      await onSubmit({ title: title.trim(), body: body.trim(), category })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Title
        </label>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-gray-800"
          placeholder="Engagement Letter Template"
          required
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Category
        </label>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-gray-800"
        >
          {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Body
        </label>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-gray-800 font-mono"
          rows={16}
          placeholder={'Dear {{client_name}},\n\nThis letter confirms...\n\nSincerely,\n{{attorney_name}}'}
          required
        />
        <p className="text-xs text-gray-500 mt-1">
          Use {'{{variable_name}}'} for placeholders.
        </p>
      </div>
      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 border border-gray-300 rounded"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={saving}
          className="px-4 py-2 text-sm text-white bg-gray-800 hover:bg-gray-900 rounded disabled:opacity-50"
        >
          {saving ? 'Saving...' : initial ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  )
}

function RenderModal({ template, onClose }) {
  const [variables, setVariables] = useState({})
  const [matterId, setMatterId] = useState('')
  const [rendered, setRendered] = useState(null)
  const [matterDocId, setMatterDocId] = useState(null)
  const [rendering, setRendering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  // Extract variable names from template body
  const varNames = useCallback(() => {
    const matches = template.body.match(/\{\{(.+?)\}\}/g) || []
    return [...new Set(matches.map((m) => m.slice(2, -2).trim()))]
  }, [template.body])

  useEffect(() => {
    const names = varNames()
    const initialVars = {}
    names.forEach((n) => {
      initialVars[n] = ''
    })
    setVariables(initialVars)
  }, [varNames])

  const handleRender = async () => {
    setRendering(true)
    try {
      const res = await renderTemplate(template.id, {
        variables,
        matter_id: matterId || null,
      })
      setRendered(res.rendered)
      if (res.matter_document_id) {
        setMatterDocId(res.matter_document_id)
      }
    } catch (err) {
      console.error('Render failed', err)
    } finally {
      setRendering(false)
    }
  }

  const handleSave = async () => {
    if (!matterId.trim()) return
    setSaving(true)
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
      console.error('Save failed', err)
    } finally {
      setSaving(false)
    }
  }

  const names = varNames()

  return (
    <Modal title={`Generate: ${template.title}`} onClose={onClose}>
      <div className="space-y-4">
        {/* Variable inputs */}
        {names.length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">
              Template Variables
            </h3>
            <div className="space-y-2">
              {names.map((name) => (
                <div key={name}>
                  <label className="block text-xs font-medium text-gray-500 mb-0.5">
                    {'{{'}{name}{'}}'}
                  </label>
                  <input
                    type="text"
                    value={variables[name] || ''}
                    onChange={(e) =>
                      setVariables((v) => ({ ...v, [name]: e.target.value }))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-gray-800"
                    placeholder={`Enter ${name}`}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {names.length === 0 && (
          <p className="text-sm text-gray-500 italic">
            This template has no variables — no substitutions needed.
          </p>
        )}

        {/* Matter ID input */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Matter ID (optional for preview, required to save)
          </label>
          <input
            type="text"
            value={matterId}
            onChange={(e) => setMatterId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-gray-800 font-mono"
            placeholder="e.g. uuid-of-matter"
          />
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={handleRender}
            disabled={rendering}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-gray-800 hover:bg-gray-900 rounded disabled:opacity-50"
          >
            <Eye size={16} />
            {rendering ? 'Rendering...' : 'Preview'}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || saved || !matterId.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded disabled:opacity-50"
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

        {/* Rendered output */}
        {rendered && (
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">
              Rendered Output
            </h3>
            <div className="bg-gray-50 border border-gray-200 rounded p-4 whitespace-pre-wrap font-mono text-sm text-gray-900 max-h-96 overflow-y-auto">
              {rendered}
            </div>
            {matterDocId && (
              <p className="text-xs text-green-600 mt-1">
                Saved as matter document: {matterDocId}
              </p>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

export default function TemplatesPage() {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [editTemplate, setEditTemplate] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [renderTarget, setRenderTarget] = useState(null)

  const load = useCallback(async () => {
    try {
      const res = await getTemplates()
      setTemplates(res.items || [])
    } catch (err) {
      console.error('Failed to load templates', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleCreate = async (data) => {
    try {
      await createTemplate(data)
      setShowCreate(false)
      await load()
    } catch (err) {
      console.error('Create failed', err)
    }
  }

  const handleUpdate = async (data) => {
    try {
      await updateTemplate(editTemplate.id, data)
      setEditTemplate(null)
      await load()
    } catch (err) {
      console.error('Update failed', err)
    }
  }

  const handleDelete = async () => {
    try {
      await deleteTemplate(deleteTarget.id)
      setDeleteTarget(null)
      await load()
    } catch (err) {
      console.error('Delete failed', err)
    }
  }

  const toggleActive = async (tpl) => {
    try {
      await updateTemplate(tpl.id, { is_active: !tpl.is_active })
      await load()
    } catch (err) {
      console.error('Toggle failed', err)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-gray-50">
        <div className="text-gray-500">Loading templates...</div>
      </div>
    )
  }

  const bodyPreview = (body) => {
    return body.length > 100 ? body.slice(0, 100) + '...' : body
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">
            Document Templates
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Create and manage reusable document templates with variable
            substitution.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-gray-800 hover:bg-gray-900 rounded"
        >
          <Plus size={18} />
          New Template
        </button>
      </div>

      {/* Template grid */}
      {templates.length === 0 ? (
        <div className="text-center py-16">
          <FileText
            size={48}
            className="mx-auto text-gray-300 mb-4"
          />
          <p className="text-gray-500">
            No templates yet. Create your first one!
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {templates.map((tpl) => (
            <div
              key={tpl.id}
              className="bg-white border border-gray-200 rounded-lg shadow-sm hover:shadow-md transition-shadow"
            >
              <div className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-medium text-gray-900 truncate">
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
                <p className="text-xs text-gray-500 font-mono mb-3">
                  {bodyPreview(tpl.body)}
                </p>

                {/* Active toggle */}
                <div className="flex items-center gap-2 mb-3">
                  <button
                    onClick={() => toggleActive(tpl)}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      tpl.is_active ? 'bg-green-500' : 'bg-gray-300'
                    }`}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                        tpl.is_active ? 'translate-x-4.5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                  <span className="text-xs text-gray-500">
                    {tpl.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
                  <button
                    onClick={() => setEditTemplate(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-gray-600 hover:text-gray-900 border border-gray-200 rounded"
                  >
                    <Pencil size={14} />
                    Edit
                  </button>
                  <button
                    onClick={() => setRenderTarget(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-gray-600 hover:text-gray-900 border border-gray-200 rounded"
                  >
                    <Sparkles size={14} />
                    Generate
                  </button>
                  <button
                    onClick={() => setDeleteTarget(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-red-600 hover:text-red-800 border border-gray-200 rounded ml-auto"
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <Modal title="Create Template" onClose={() => setShowCreate(false)}>
          <TemplateForm
            onSubmit={handleCreate}
            onCancel={() => setShowCreate(false)}
          />
        </Modal>
      )}

      {/* Edit Modal */}
      {editTemplate && (
        <Modal title="Edit Template" onClose={() => setEditTemplate(null)}>
          <TemplateForm
            initial={editTemplate}
            onSubmit={handleUpdate}
            onCancel={() => setEditTemplate(null)}
          />
        </Modal>
      )}

      {/* Delete Confirmation */}
      {deleteTarget && (
        <ConfirmDialog
          message={`Delete "${deleteTarget.title}"? This cannot be undone.`}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {/* Render Modal */}
      {renderTarget && (
        <RenderModal
          template={renderTarget}
          onClose={() => setRenderTarget(null)}
        />
      )}
    </div>
  )
}
