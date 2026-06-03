import React, { useState, useEffect, useRef } from 'react'
import { format, parseISO } from 'date-fns'
import {
  getMatterDocuments,
  uploadMatterDocument,
  updateMatterDocument,
  deleteMatterDocument,
  getMatterDocumentDownloadUrl,
} from '../api'
import { FileText, Upload, Trash2, Download, X, Check } from 'lucide-react'

const CATEGORIES = ['pleading', 'contract', 'evidence', 'correspondence', 'other']

const CATEGORY_COLORS = {
  pleading: 'bg-blue-100 text-blue-800 border-blue-200',
  contract: 'bg-purple-100 text-purple-800 border-purple-200',
  evidence: 'bg-orange-100 text-orange-800 border-orange-200',
  correspondence: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
  other: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

function CategoryBadge({ category }) {
  const cls = CATEGORY_COLORS[category] || CATEGORY_COLORS.other
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}
    >
      {category || 'other'}
    </span>
  )
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function MatterDocumentsTab({ matterId }) {
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Upload form state
  const [showUpload, setShowUpload] = useState(false)
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadCategory, setUploadCategory] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const fileInputRef = useRef(null)

  // Inline edit state
  const [editingId, setEditingId] = useState(null)
  const [editDescription, setEditDescription] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [saving, setSaving] = useState(false)

  const inputClasses =
    'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelClasses =
    'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5'

  useEffect(() => {
    getMatterDocuments(matterId)
      .then((data) => setDocs(data.items || []))
      .catch(() => setError('Failed to load documents.'))
      .finally(() => setLoading(false))
  }, [matterId])

  const handleUpload = async () => {
    if (!uploadFile) return
    setUploading(true)
    setUploadError(null)
    try {
      const formData = new FormData()
      formData.append('file', uploadFile)
      if (uploadDescription) formData.append('description', uploadDescription)
      if (uploadCategory) formData.append('document_category', uploadCategory)
      const newDoc = await uploadMatterDocument(matterId, formData)
      setDocs((prev) => [newDoc, ...prev])
      setShowUpload(false)
      setUploadFile(null)
      setUploadDescription('')
      setUploadCategory('')
      if (fileInputRef.current) fileInputRef.current.value = ''
    } catch (err) {
      const msg =
        err?.response?.data?.detail || 'Upload failed. Check file size (max 50MB).'
      setUploadError(msg)
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (docId) => {
    if (!window.confirm('Delete this document? This cannot be undone.')) return
    try {
      await deleteMatterDocument(matterId, docId)
      setDocs((prev) => prev.filter((d) => d.id !== docId))
    } catch {
      alert('Failed to delete document.')
    }
  }

  const startEdit = (doc) => {
    setEditingId(doc.id)
    setEditDescription(doc.description || '')
    setEditCategory(doc.document_category || '')
  }

  const cancelEdit = () => {
    setEditingId(null)
  }

  const saveEdit = async (docId) => {
    setSaving(true)
    try {
      const updated = await updateMatterDocument(matterId, docId, {
        description: editDescription || null,
        document_category: editCategory || null,
      })
      setDocs((prev) => prev.map((d) => (d.id === docId ? updated : d)))
      setEditingId(null)
    } catch {
      alert('Failed to save changes.')
    } finally {
      setSaving(false)
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
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
          <FileText size={20} className="text-brand-accent" /> Case Documents
          <span className="ml-2 text-[13px] font-sans font-medium text-brand-muted">
            ({docs.length})
          </span>
        </h2>
        <button
          onClick={() => setShowUpload((v) => !v)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm"
        >
          <Upload size={16} /> Upload Document
        </button>
      </div>

      {/* Upload form */}
      {showUpload && (
        <div className="bg-brand-bg border border-brand-line rounded-xl p-6 space-y-4">
          <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest">
            Upload File
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <label className={labelClasses}>File *</label>
              <input
                ref={fileInputRef}
                type="file"
                onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                className="w-full text-[14px] font-sans text-brand-ink file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border file:border-brand-line file:text-sm file:font-medium file:bg-brand-surface file:text-brand-ink hover:file:bg-brand-bg-soft cursor-pointer"
              />
            </div>
            <div>
              <label className={labelClasses}>Category</label>
              <select
                value={uploadCategory}
                onChange={(e) => setUploadCategory(e.target.value)}
                className={inputClasses}
              >
                <option value="">— select —</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c.charAt(0).toUpperCase() + c.slice(1)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClasses}>Description</label>
              <input
                type="text"
                value={uploadDescription}
                onChange={(e) => setUploadDescription(e.target.value)}
                placeholder="Optional description"
                className={inputClasses}
              />
            </div>
          </div>
          {uploadError && (
            <p className="text-brand-rose text-sm font-sans bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">
              {uploadError}
            </p>
          )}
          <div className="flex gap-3 justify-end">
            <button
              onClick={() => {
                setShowUpload(false)
                setUploadFile(null)
                setUploadError(null)
              }}
              className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans hover:text-brand-ink transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleUpload}
              disabled={!uploadFile || uploading}
              className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all shadow-sm flex items-center gap-2"
            >
              {uploading ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Uploading…
                </>
              ) : (
                <>
                  <Upload size={15} /> Upload
                </>
              )}
            </button>
          </div>
        </div>
      )}

      {/* Documents table */}
      {docs.length === 0 ? (
        <div className="text-center py-16 bg-brand-surface border border-brand-line rounded-2xl">
          <FileText
            size={32}
            className="mx-auto text-brand-line-2 mb-3"
            strokeWidth={1.5}
          />
          <p className="text-brand-ink font-serif text-lg font-bold mb-1">
            No documents attached
          </p>
          <p className="text-brand-muted text-sm font-sans">
            Upload pleadings, contracts, evidence, and correspondence here.
          </p>
        </div>
      ) : (
        <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
          <table className="w-full text-[13px] font-sans">
            <thead>
              <tr className="border-b border-brand-line bg-brand-bg-soft/50">
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Filename
                </th>
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Category
                </th>
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Size
                </th>
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Description
                </th>
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Uploaded
                </th>
                <th className="px-5 py-3.5" />
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr
                  key={doc.id}
                  className="border-b border-brand-line/50 last:border-0 hover:bg-brand-bg-soft/40 transition-colors"
                >
                  {editingId === doc.id ? (
                    <>
                      <td className="px-5 py-3 font-medium text-brand-ink">
                        {doc.filename}
                      </td>
                      <td className="px-5 py-3">
                        <select
                          value={editCategory}
                          onChange={(e) => setEditCategory(e.target.value)}
                          className="border border-brand-line rounded px-2 py-1 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent"
                        >
                          <option value="">— none —</option>
                          {CATEGORIES.map((c) => (
                            <option key={c} value={c}>
                              {c.charAt(0).toUpperCase() + c.slice(1)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-5 py-3 text-brand-muted">
                        {formatBytes(doc.file_size)}
                      </td>
                      <td className="px-5 py-3" colSpan={2}>
                        <input
                          type="text"
                          value={editDescription}
                          onChange={(e) => setEditDescription(e.target.value)}
                          placeholder="Description"
                          className="w-full border border-brand-line rounded px-2 py-1 text-[13px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent"
                        />
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 justify-end">
                          <button
                            onClick={() => saveEdit(doc.id)}
                            disabled={saving}
                            className="text-brand-green hover:text-brand-green/80 transition-colors disabled:opacity-50"
                            title="Save"
                          >
                            <Check size={16} />
                          </button>
                          <button
                            onClick={cancelEdit}
                            className="text-brand-muted hover:text-brand-ink transition-colors"
                            title="Cancel"
                          >
                            <X size={16} />
                          </button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="px-5 py-3 font-medium text-brand-ink max-w-[200px] truncate">
                        <button
                          onClick={() => startEdit(doc)}
                          className="hover:text-brand-accent transition-colors text-left"
                          title="Click to edit"
                        >
                          {doc.filename}
                        </button>
                      </td>
                      <td className="px-5 py-3">
                        {doc.document_category ? (
                          <CategoryBadge category={doc.document_category} />
                        ) : (
                          <span className="text-brand-muted">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-brand-muted">
                        {formatBytes(doc.file_size)}
                      </td>
                      <td className="px-5 py-3 text-brand-ink-2 max-w-[200px] truncate">
                        {doc.description || (
                          <span className="text-brand-muted">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-brand-muted whitespace-nowrap">
                        {doc.created_at
                          ? (() => {
                              try {
                                return format(parseISO(doc.created_at), 'MMM d, yyyy')
                              } catch {
                                return doc.created_at
                              }
                            })()
                          : '—'}
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 justify-end">
                          <a
                            href={getMatterDocumentDownloadUrl(matterId, doc.id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-brand-muted hover:text-brand-ink transition-colors"
                            title="Download"
                          >
                            <Download size={16} />
                          </a>
                          <button
                            onClick={() => handleDelete(doc.id)}
                            className="text-brand-muted hover:text-brand-rose transition-colors"
                            title="Delete"
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
