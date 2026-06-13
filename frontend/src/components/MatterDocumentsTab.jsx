import React, { useState, useEffect, useRef, useCallback } from 'react'
import { format, parseISO } from 'date-fns'
import {
  getMatterDocuments,
  uploadMatterDocument,
  updateMatterDocument,
  deleteMatterDocument,
  getMatterDocumentDownloadUrl,
  getMatterCloudFolder,
  provisionMatterCloudFolder,
  syncMatterCloudFolder,
  getMatterCloudFiles,
} from '../api'
import { FileText, Upload, Trash2, Download, X, Check, Cloud, ExternalLink, RefreshCw, Eye, EyeOff } from 'lucide-react'

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

function storageLabel(backend) {
  if (backend === 'google_drive') return 'Google Drive'
  if (backend === 'onedrive') return 'OneDrive'
  if (backend === 'cloud') return 'Cloud'
  return 'Local'
}

function CloudFolderCard({ matterId, onFolderChange, onSynced }) {
  const [status, setStatus] = useState(null)
  const [provisioning, setProvisioning] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [toast, setToast] = useState(null)

  useEffect(() => {
    getMatterCloudFolder(matterId).then(setStatus).catch(() => {})
  }, [matterId])

  const handleProvision = useCallback(async () => {
    setProvisioning(true)
    setToast(null)
    try {
      const result = await provisionMatterCloudFolder(matterId)
      setStatus(result)
      onFolderChange?.(result.providers || {})
      setToast({ type: 'success', msg: 'Cloud folder set up successfully.' })
    } catch (err) {
      setToast({ type: 'error', msg: err?.response?.data?.detail || 'Provisioning failed.' })
    } finally {
      setProvisioning(false)
    }
  }, [matterId, onFolderChange])

  const handleSync = useCallback(async () => {
    setSyncing(true)
    setToast(null)
    try {
      const result = await syncMatterCloudFolder(matterId)
      setStatus({
        status: result.providers ? 'provisioned' : result.status,
        providers: result.providers || {},
      })
      onFolderChange?.(result.providers || {})
      onSynced?.(result.files || [])
      setToast({
        type: 'success',
        msg: `Synced ${result.files?.length ?? 0} cloud file${result.files?.length === 1 ? '' : 's'}.`,
      })
    } catch (err) {
      setToast({ type: 'error', msg: err?.response?.data?.detail || 'Cloud sync failed.' })
    } finally {
      setSyncing(false)
    }
  }, [matterId, onFolderChange, onSynced])

  if (!status) return null

  const od = status.providers?.onedrive
  const gd = status.providers?.google_drive
  const isProvisioned = status.status === 'provisioned' && (od || gd)

  return (
    <div className="bg-brand-bg border border-brand-line rounded-xl p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Cloud size={16} className="text-brand-accent" />
        <span className="text-[13px] font-bold font-sans text-brand-ink uppercase tracking-wider">Cloud Storage</span>
      </div>

      {isProvisioned ? (
        <div className="flex flex-wrap items-center gap-3">
          {od?.url && (
            <a
              href={od.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-[13px] font-sans text-brand-accent hover:underline"
            >
              <ExternalLink size={13} /> Open in OneDrive
            </a>
          )}
          {gd?.url && (
            <a
              href={gd.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-[13px] font-sans text-brand-accent hover:underline"
            >
              <ExternalLink size={13} /> Open in Google Drive
            </a>
          )}
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing…' : 'Sync folder'}
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-3">
          <span className="text-[13px] font-sans text-brand-muted">Cloud folder not provisioned</span>
          <button
            onClick={handleProvision}
            disabled={provisioning}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-accent text-white text-[12px] font-sans font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {provisioning ? <RefreshCw size={12} className="animate-spin" /> : <Cloud size={12} />}
            {provisioning ? 'Setting up…' : 'Set Up Cloud Folder'}
          </button>
          <button
            onClick={handleSync}
            disabled={syncing || provisioning}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing…' : 'Provision + Sync'}
          </button>
        </div>
      )}

      {toast && (
        <p className={`text-[12px] font-sans ${toast.type === 'error' ? 'text-brand-rose' : 'text-green-600'}`}>
          {toast.msg}
        </p>
      )}
    </div>
  )
}

export default function MatterDocumentsTab({ matterId, onCloudFolderChange }) {
  const [docs, setDocs] = useState([])
  const [cloudFiles, setCloudFiles] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Upload form state
  const [showUpload, setShowUpload] = useState(false)
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadCategory, setUploadCategory] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [uploadNotice, setUploadNotice] = useState(null)
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
    Promise.all([
      getMatterDocuments(matterId),
      getMatterCloudFiles(matterId).catch(() => null),
    ])
      .then(([data, cloudData]) => {
        setDocs(data.items || [])
        if (cloudData) setCloudFiles(cloudData.files || [])
      })
      .catch(() => setError('Failed to load documents.'))
      .finally(() => setLoading(false))
  }, [matterId])

  const handleUpload = async () => {
    if (!uploadFile) return
    setUploading(true)
    setUploadError(null)
    setUploadNotice(null)
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
      if (newDoc.cloud_url) {
        setUploadNotice({
          type: 'success',
          text: `Saved to ${storageLabel(newDoc.storage_backend)}. Edits happen in the firm's cloud copy.`,
        })
      } else {
        setUploadNotice({
          type: 'warning',
          text: 'Saved locally because no writable cloud folder was available.',
        })
      }
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

  const handleTogglePortalVisible = async (doc) => {
    const newValue = !doc.portal_visible
    // Optimistic update
    setDocs((prev) =>
      prev.map((d) => (d.id === doc.id ? { ...d, portal_visible: newValue } : d))
    )
    try {
      const updated = await updateMatterDocument(matterId, doc.id, {
        portal_visible: newValue,
      })
      setDocs((prev) => prev.map((d) => (d.id === doc.id ? updated : d)))
    } catch {
      // Revert optimistic update on error
      setDocs((prev) =>
        prev.map((d) => (d.id === doc.id ? { ...d, portal_visible: doc.portal_visible } : d))
      )
      setError('Failed to update portal visibility. Please try again.')
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
      {/* Cloud folder status */}
      <CloudFolderCard
        matterId={matterId}
        onFolderChange={onCloudFolderChange}
        onSynced={setCloudFiles}
      />

      {cloudFiles?.length > 0 && (
        <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
          <div className="px-5 py-3.5 border-b border-brand-line bg-brand-bg-soft/50 flex items-center justify-between">
            <h3 className="text-[13px] font-bold font-sans text-brand-ink uppercase tracking-wider">
              Synced Cloud Files
            </h3>
            <span className="text-[12px] text-brand-muted font-sans">{cloudFiles.length}</span>
          </div>
          <div className="divide-y divide-brand-line/60">
            {cloudFiles.slice(0, 12).map((file, i) => (
              <a
                key={file.id || i}
                href={file.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 px-5 py-3 hover:bg-brand-bg-soft transition-colors"
              >
                <Cloud size={15} className="text-brand-accent shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-semibold text-brand-ink font-sans truncate">
                    {file.title}
                  </div>
                  <div className="text-[11px] text-brand-muted font-sans uppercase tracking-wide">
                    {file.provider} / {file.source}
                  </div>
                </div>
                <ExternalLink size={13} className="text-brand-muted shrink-0" />
              </a>
            ))}
          </div>
        </div>
      )}

      {uploadNotice && (
        <div
          className={`border rounded-xl px-4 py-3 text-[13px] font-sans ${
            uploadNotice.type === 'success'
              ? 'bg-brand-green/10 border-brand-green/25 text-brand-green'
              : 'bg-brand-amber/10 border-brand-amber/25 text-brand-ink'
          }`}
        >
          {uploadNotice.text}
        </div>
      )}

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
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Client Portal
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
                        {doc.portal_visible ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-brand-green/10 text-brand-green border border-brand-green/30">
                            <Eye size={11} /> Shared
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-brand-bg-soft text-brand-muted border border-brand-line">
                            <EyeOff size={11} /> Private
                          </span>
                        )}
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
                        {doc.cloud_url && (
                          <a
                            href={doc.cloud_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="mt-1 flex items-center gap-1 text-[11px] font-sans text-brand-accent hover:underline"
                            title={`Open in ${storageLabel(doc.storage_backend)}`}
                          >
                            <ExternalLink size={11} /> Open in {storageLabel(doc.storage_backend)}
                          </a>
                        )}
                      </td>
                      <td className="px-5 py-3">
                        {doc.document_category ? (
                          <CategoryBadge category={doc.document_category} />
                        ) : (
                          <span className="text-brand-muted">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-brand-muted">
                        <div>{formatBytes(doc.file_size)}</div>
                        <div
                          className={`mt-1 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide ${
                            doc.cloud_url ? 'text-brand-accent' : 'text-brand-muted'
                          }`}
                        >
                          {doc.cloud_url && <Cloud size={10} />}
                          {storageLabel(doc.storage_backend)}
                        </div>
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
                        <button
                          onClick={() => handleTogglePortalVisible(doc)}
                          aria-label={doc.portal_visible ? 'Make private' : 'Share with client'}
                          title={doc.portal_visible ? 'Make private' : 'Share with client'}
                          className="flex items-center gap-1.5 group"
                        >
                          {doc.portal_visible ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-brand-green/10 text-brand-green border border-brand-green/30 group-hover:bg-brand-green/20 transition-colors">
                              <Eye size={11} /> Shared with client
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-brand-bg-soft text-brand-muted border border-brand-line group-hover:border-brand-accent group-hover:text-brand-accent transition-colors">
                              <EyeOff size={11} /> Private
                            </span>
                          )}
                        </button>
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 justify-end">
                          <a
                            href={doc.cloud_url || getMatterDocumentDownloadUrl(matterId, doc.id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-brand-muted hover:text-brand-ink transition-colors"
                            title={doc.cloud_url ? `Open in ${storageLabel(doc.storage_backend)}` : 'Download'}
                          >
                            {doc.cloud_url ? <ExternalLink size={16} /> : <Download size={16} />}
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
