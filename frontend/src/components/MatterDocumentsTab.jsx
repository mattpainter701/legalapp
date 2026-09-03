import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { format, parseISO } from 'date-fns'
import {
  uploadMatterDocument,
  updateMatterDocument,
  deleteMatterDocument,
  getMatterDocumentDownloadUrl,
  getMatterCloudFolder,
  provisionMatterCloudFolder,
  syncMatterCloudFolder,
  getMatterCloudFiles,
  createDocumentTag,
  setMatterDocumentTags,
} from '../api'
import { FileText, Upload, Trash2, Download, X, Check, Cloud, ExternalLink, RefreshCw, Eye, EyeOff, Sparkles, Pencil, ShieldCheck, Folder, Search, Tag as TagIcon } from 'lucide-react'
import { useConfirm } from './dialog/ConfirmProvider'
import { useToast } from './toast/useToast'
import useMatterDocumentExplorer, {
  ALL_DOCUMENTS,
  ROOT_FOLDER,
} from '../hooks/useMatterDocumentExplorer'
import DocumentFolderTree, {
  writeDraggedDocumentIds,
} from './documents/DocumentFolderTree'
import {
  DocumentTagEditor,
  DocumentTagFilter,
  TagChip,
} from './documents/DocumentTags'

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

function apiErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (typeof detail?.message === 'string') return detail.message
  return error?.message || fallback
}

const DOCX_MIME_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

export function canReviseWithAssistant(document) {
  const filename = String(document?.filename || '').toLowerCase()
  const contentType = String(document?.content_type || document?.mime_type || '').toLowerCase()
  return filename.endsWith('.docx') || contentType === DOCX_MIME_TYPE
}

export function isAssistantRevisionDocument(document) {
  return String(document?.document_category || '').toLowerCase() === 'assistant_revision'
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
      setToast({ type: 'error', msg: apiErrorMessage(err, 'Provisioning failed.') })
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
      setToast({ type: 'error', msg: apiErrorMessage(err, 'Cloud sync failed.') })
    } finally {
      setSyncing(false)
    }
  }, [matterId, onFolderChange, onSynced])

  if (!status) return null

  const providers = status.providers || {}
  const od = providers.onedrive
  const gd = providers.google_drive
  const provStatus = providers._status
  const provMessage = providers._status_message
  const isProvisioned = status.status === 'provisioned' && (od || gd)
  const isFailed = provStatus === 'failed'

  return (
    <div className="bg-brand-bg border border-brand-line rounded-xl p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Cloud size={16} className="text-brand-accent" />
        <span className="text-[13px] font-bold font-sans text-brand-ink uppercase tracking-wider">Cloud Storage</span>
        {isFailed && (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold font-sans bg-red-100 text-red-700 border border-red-200">
            Failed
          </span>
        )}
      </div>

      {isFailed && (
        <p className="text-[12px] font-sans text-red-600 bg-red-50 border border-red-100 rounded-lg p-3">
          Provisioning failed: {provMessage || 'Unknown error'}. You can retry below.
        </p>
      )}

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
          <span className="text-[13px] font-sans text-brand-muted">
            {isFailed ? 'Cloud folder provisioning failed' : 'Cloud folder not provisioned'}
          </span>
          {isFailed && (
            <span className="text-[11px] font-sans text-brand-muted">
              Check your cloud connection in Settings → File Shares, or use "Provision + Sync" to retry.
            </span>
          )}
          <button
            onClick={handleProvision}
            disabled={provisioning}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-accent text-white text-[12px] font-sans font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {provisioning ? <RefreshCw size={12} className="animate-spin" /> : <Cloud size={12} />}
            {provisioning ? 'Setting up…' : isFailed ? 'Retry Setup' : 'Set Up Cloud Folder'}
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

export default function MatterDocumentsTab({ matterId, onCloudFolderChange, onReviseDocument }) {
  const confirmAction = useConfirm()
  const toast = useToast()
  const explorer = useMatterDocumentExplorer(matterId)
  const {
    documents: docs,
    setDocuments: setDocs,
    folders,
    foldersByParent,
    tags,
    rootDocumentCount,
    currentFolder,
    breadcrumb,
    loading,
    listing,
    error,
    setError,
    folderId,
    setFolderId,
    includeSubfolders,
    setIncludeSubfolders,
    search,
    setSearch,
    selectedTagIds,
    setSelectedTagIds,
    toggleTagFilter,
    sort,
    setSort,
    order,
    setOrder,
    createFolder,
    renameFolder,
    removeFolder,
    fileDocuments,
    refreshFolders,
    refreshTags,
    refreshDocuments,
  } = explorer
  const [cloudFiles, setCloudFiles] = useState(null)

  // Upload form state
  const [showUpload, setShowUpload] = useState(false)
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadCategory, setUploadCategory] = useState('')
  const [uploadFolderId, setUploadFolderId] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [uploadNotice, setUploadNotice] = useState(null)
  const fileInputRef = useRef(null)

  // Inline edit state
  const [editingId, setEditingId] = useState(null)
  const [editDescription, setEditDescription] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [saving, setSaving] = useState(false)

  // Folder name entry (create / rename) and tag popover
  const [folderDraft, setFolderDraft] = useState(null)
  const [folderDraftError, setFolderDraftError] = useState(null)
  const [tagEditorDocId, setTagEditorDocId] = useState(null)

  // New uploads default into the folder the user is looking at, which is
  // almost always where they mean to put the file.
  useEffect(() => {
    if (showUpload) {
      setUploadFolderId(
        folderId === ALL_DOCUMENTS || folderId === ROOT_FOLDER ? '' : folderId,
      )
    }
  }, [showUpload, folderId])

  const totalDocumentCount = useMemo(
    () => folders.reduce((sum, f) => sum + (f.document_count || 0), rootDocumentCount),
    [folders, rootDocumentCount],
  )

  const folderOptions = useMemo(
    () => [...folders].sort((a, b) => a.path.localeCompare(b.path)),
    [folders],
  )

  const inputClasses =
    'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelClasses =
    'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5'

  useEffect(() => {
    getMatterCloudFiles(matterId)
      .then((cloudData) => {
        if (cloudData) setCloudFiles(cloudData.files || [])
      })
      .catch(() => {})
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
      if (uploadFolderId) formData.append('folder_id', uploadFolderId)
      const newDoc = await uploadMatterDocument(matterId, formData)
      setDocs((prev) => [newDoc, ...prev])
      // The optimistic row is only correct when the upload landed in the view
      // the user is looking at; re-listing settles the case where they filed it
      // into a different folder, and refreshes the rail's counts either way.
      await refreshFolders()
      await refreshDocuments()
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
      setUploadError(apiErrorMessage(err, 'Upload failed. Check file size (max 50MB).'))
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (docId) => {
    if (!await confirmAction({ title: 'Delete document?', message: 'This document will be permanently deleted.', confirmLabel: 'Delete document', destructive: true })) return
    try {
      await deleteMatterDocument(matterId, docId)
      setDocs((prev) => prev.filter((d) => d.id !== docId))
      await refreshFolders()
    } catch (error) {
      toast.error('Document was not deleted', { message: apiErrorMessage(error, 'Please try again.') })
    }
  }

  // ── Folders ───────────────────────────────────────────────────────────────

  const startCreateFolder = useCallback((parentId) => {
    setFolderDraftError(null)
    setFolderDraft({ mode: 'create', parentId: parentId || null, name: '' })
  }, [])

  const startRenameFolder = useCallback((folder) => {
    setFolderDraftError(null)
    setFolderDraft({ mode: 'rename', folderId: folder.id, name: folder.name })
  }, [])

  const submitFolderDraft = useCallback(async () => {
    if (!folderDraft?.name.trim()) return
    setFolderDraftError(null)
    try {
      if (folderDraft.mode === 'create') {
        const folder = await createFolder(folderDraft.name.trim(), folderDraft.parentId)
        setFolderId(folder.id)
      } else {
        await renameFolder(folderDraft.folderId, folderDraft.name.trim())
      }
      setFolderDraft(null)
    } catch (err) {
      setFolderDraftError(apiErrorMessage(err, 'That folder could not be saved.'))
    }
  }, [folderDraft, createFolder, renameFolder, setFolderId])

  const handleDeleteFolder = useCallback(
    async (folder) => {
      const holdsDocuments = (folder.document_count || 0) > 0
      const confirmed = await confirmAction({
        title: `Delete "${folder.name}"?`,
        message: holdsDocuments
          ? `This folder and its subfolders will be removed. Its ${folder.document_count} document(s) are kept and moved up one level.`
          : 'This folder and its subfolders will be removed. Documents are never deleted with a folder.',
        confirmLabel: 'Delete folder',
        destructive: true,
      })
      if (!confirmed) return
      try {
        const result = await removeFolder(folder.id, { moveDocumentsToParent: true })
        if (result.documents_moved) {
          toast.success(
            `Moved ${result.documents_moved} document${result.documents_moved === 1 ? '' : 's'} up one level.`,
          )
        }
      } catch (err) {
        toast.error('Folder was not deleted', {
          message: apiErrorMessage(err, 'Please try again.'),
        })
      }
    },
    [confirmAction, removeFolder, toast],
  )

  const handleDropDocuments = useCallback(
    async (documentIds, targetFolderId) => {
      try {
        await fileDocuments(documentIds, targetFolderId)
      } catch (err) {
        toast.error('Documents were not moved', {
          message: apiErrorMessage(err, 'Please try again.'),
        })
      }
    },
    [fileDocuments, toast],
  )

  // ── Tags ──────────────────────────────────────────────────────────────────

  const handleCreateTag = useCallback(
    async (name) => {
      const tag = await createDocumentTag({ name })
      await refreshTags()
      return tag
    },
    [refreshTags],
  )

  const handleApplyTags = useCallback(
    async (docId, tagIds) => {
      const result = await setMatterDocumentTags(matterId, docId, tagIds)
      setDocs((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, tags: result.items || [] } : d)),
      )
      // A document may drop out of an active tag filter once its tags change.
      if (selectedTagIds.length) await refreshDocuments()
    },
    [matterId, setDocs, selectedTagIds, refreshDocuments],
  )

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
    } catch (error) {
      toast.error('Document changes were not saved', { message: apiErrorMessage(error, 'Please try again.') })
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
        <div className="flex items-center gap-2">
          <button
            onClick={() => startCreateFolder(folderId === ALL_DOCUMENTS || folderId === ROOT_FOLDER ? null : folderId)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm"
          >
            <Folder size={16} /> New Folder
          </button>
          <button
            onClick={() => setShowUpload((v) => !v)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm"
          >
            <Upload size={16} /> Upload Document
          </button>
        </div>
      </div>

      {/* Folder name entry — create or rename */}
      {folderDraft && (
        <div className="bg-brand-bg border border-brand-line rounded-xl p-4 space-y-3">
          <label htmlFor="matterdocumentstab-folder-name" className={labelClasses}>
            {folderDraft.mode === 'create' ? 'New folder name' : 'Rename folder'}
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <input
              id="matterdocumentstab-folder-name"
              autoFocus
              value={folderDraft.name}
              onChange={(e) => setFolderDraft({ ...folderDraft, name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); submitFolderDraft() }
                if (e.key === 'Escape') setFolderDraft(null)
              }}
              placeholder="e.g. Discovery"
              className={`${inputClasses} max-w-xs`}
            />
            <button
              type="button"
              onClick={submitFolderDraft}
              disabled={!folderDraft.name.trim()}
              className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:opacity-50"
            >
              {folderDraft.mode === 'create' ? 'Create folder' : 'Save name'}
            </button>
            <button
              type="button"
              onClick={() => setFolderDraft(null)}
              className="px-4 py-2.5 text-brand-ink-2 text-sm font-sans hover:text-brand-ink"
            >
              Cancel
            </button>
          </div>
          {folderDraftError && (
            <p className="text-brand-rose text-sm font-sans">{folderDraftError}</p>
          )}
        </div>
      )}

      {/* Upload form */}
      {showUpload && (
        <div className="bg-brand-bg border border-brand-line rounded-xl p-6 space-y-4">
          <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest">
            Upload File
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <label htmlFor="matterdocumentstab-file" className={labelClasses}>File *</label>
              <input id="matterdocumentstab-file"
                ref={fileInputRef}
                type="file"
                onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                className="w-full text-[14px] font-sans text-brand-ink file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border file:border-brand-line file:text-sm file:font-medium file:bg-brand-surface file:text-brand-ink hover:file:bg-brand-bg-soft cursor-pointer"
              />
            </div>
            <div>
              <label htmlFor="matterdocumentstab-category" className={labelClasses}>Category</label>
              <select id="matterdocumentstab-category"
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
              <label htmlFor="matterdocumentstab-description" className={labelClasses}>Description</label>
              <input id="matterdocumentstab-description"
                type="text"
                value={uploadDescription}
                onChange={(e) => setUploadDescription(e.target.value)}
                placeholder="Optional description"
                className={inputClasses}
              />
            </div>
            <div className="md:col-span-2">
              <label htmlFor="matterdocumentstab-folder" className={labelClasses}>Folder</label>
              <select id="matterdocumentstab-folder"
                value={uploadFolderId}
                onChange={(e) => setUploadFolderId(e.target.value)}
                className={inputClasses}
              >
                <option value="">Unfiled (matter root)</option>
                {folderOptions.map((f) => (
                  <option key={f.id} value={f.id}>{f.path}</option>
                ))}
              </select>
              <p className="mt-1.5 text-[11px] font-sans text-brand-muted">
                The file is written to the matching folder in the firm's cloud
                share, so the share mirrors what you see here.
              </p>
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

      {/* Explorer: folder rail + filtered document list */}
      <div className="grid gap-5 md:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="rounded-2xl border border-brand-line bg-brand-surface p-3 shadow-sm md:sticky md:top-4 md:self-start">
          <DocumentFolderTree
            folders={folders}
            foldersByParent={foldersByParent}
            rootDocumentCount={rootDocumentCount}
            totalDocumentCount={totalDocumentCount}
            selectedFolderId={folderId}
            onSelectFolder={setFolderId}
            onCreateFolder={startCreateFolder}
            onRenameFolder={startRenameFolder}
            onDeleteFolder={handleDeleteFolder}
            onDropDocuments={handleDropDocuments}
          />
        </aside>

        <div className="min-w-0 space-y-4">
          {/* Toolbar: location, search, tags, sort */}
          <div className="space-y-3 rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-1 text-[13px] font-sans">
                <button
                  type="button"
                  onClick={() => setFolderId(ALL_DOCUMENTS)}
                  className={`rounded px-1.5 py-0.5 ${folderId === ALL_DOCUMENTS ? 'font-bold text-brand-ink' : 'text-brand-accent hover:underline'}`}
                >
                  All documents
                </button>
                {folderId === ROOT_FOLDER && (
                  <>
                    <span className="text-brand-muted" aria-hidden="true">/</span>
                    <span className="font-bold text-brand-ink">Unfiled</span>
                  </>
                )}
                {breadcrumb.map((node, index) => (
                  <span key={node.id} className="flex items-center gap-1">
                    <span className="text-brand-muted" aria-hidden="true">/</span>
                    <button
                      type="button"
                      onClick={() => setFolderId(node.id)}
                      className={`rounded px-1.5 py-0.5 ${index === breadcrumb.length - 1 ? 'font-bold text-brand-ink' : 'text-brand-accent hover:underline'}`}
                    >
                      {node.name}
                    </button>
                  </span>
                ))}
              </nav>

              <div className="flex flex-wrap items-center gap-2">
                {currentFolder && (
                  <label className="flex items-center gap-1.5 text-[12px] font-sans text-brand-ink-2">
                    <input
                      type="checkbox"
                      checked={includeSubfolders}
                      onChange={(e) => setIncludeSubfolders(e.target.checked)}
                      className="h-3.5 w-3.5"
                    />
                    Include subfolders
                  </label>
                )}
                <label className="sr-only" htmlFor="matterdocumentstab-sort">Sort documents by</label>
                <select
                  id="matterdocumentstab-sort"
                  value={`${sort}:${order}`}
                  onChange={(e) => {
                    const [nextSort, nextOrder] = e.target.value.split(':')
                    setSort(nextSort)
                    setOrder(nextOrder)
                  }}
                  className="rounded-lg border border-brand-line bg-brand-surface px-2.5 py-1.5 text-[13px] font-sans text-brand-ink"
                >
                  <option value="created_at:desc">Newest first</option>
                  <option value="created_at:asc">Oldest first</option>
                  <option value="filename:asc">Name A–Z</option>
                  <option value="filename:desc">Name Z–A</option>
                  <option value="file_size:desc">Largest first</option>
                  <option value="file_size:asc">Smallest first</option>
                </select>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <div className="relative min-w-[12rem] flex-1">
                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" aria-hidden="true" />
                <label className="sr-only" htmlFor="matterdocumentstab-search">Search documents</label>
                <input
                  id="matterdocumentstab-search"
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search filename or description"
                  className="w-full rounded-lg border border-brand-line bg-brand-surface py-1.5 pl-8 pr-3 text-[13px] font-sans text-brand-ink focus:border-brand-accent focus:outline-none"
                />
              </div>
              <DocumentTagFilter
                tags={tags}
                selectedTagIds={selectedTagIds}
                onToggle={toggleTagFilter}
                onClear={() => setSelectedTagIds([])}
              />
            </div>
          </div>

      {/* Documents table */}
      {listing && docs.length === 0 ? (
        <div className="py-10 text-center text-[13px] font-sans text-brand-muted">Loading documents…</div>
      ) : docs.length === 0 ? (
        <div className="text-center py-16 bg-brand-surface border border-brand-line rounded-2xl">
          <FileText
            size={32}
            className="mx-auto text-brand-line-2 mb-3"
            strokeWidth={1.5}
          />
          <p className="text-brand-ink font-serif text-lg font-bold mb-1">
            {search || selectedTagIds.length ? 'No matching documents' : 'No documents here'}
          </p>
          <p className="text-brand-muted text-sm font-sans">
            {search || selectedTagIds.length
              ? 'Try a different search or clear the tag filter.'
              : currentFolder
                ? `Upload into "${currentFolder.name}", or drag documents onto it from another folder.`
                : 'Upload pleadings, contracts, evidence, and correspondence here.'}
          </p>
        </div>
      ) : (
        <>
        <div className="space-y-3 md:hidden" aria-label="Case documents">
          {docs.map((doc) => {
            const revisionAvailable = canReviseWithAssistant(doc)
            const releaseLocked = isAssistantRevisionDocument(doc)
            const unsupportedReasonId = `assistant-revision-unavailable-${doc.id}`
            const releaseLockedReasonId = `assistant-release-locked-${doc.id}`
            return (
              <article key={doc.id} className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm">
                <div className="flex items-start gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-accent">
                    <FileText size={18} aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="break-words text-sm font-bold text-brand-ink">{doc.filename}</h3>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {doc.document_category ? <CategoryBadge category={doc.document_category} /> : <span className="text-xs text-brand-muted">Uncategorized</span>}
                      <span className="text-xs text-brand-muted">{formatBytes(doc.file_size)}</span>
                      <span className="text-xs text-brand-muted">{storageLabel(doc.storage_backend)}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="inline-flex items-center gap-1 text-xs text-brand-muted">
                        <Folder size={11} aria-hidden="true" /> {doc.folder_path || 'Unfiled'}
                      </span>
                      {(doc.tags || []).map((tag) => (
                        <TagChip key={tag.id} tag={tag} />
                      ))}
                    </div>
                  </div>
                </div>

                {editingId === doc.id ? (
                  <div className="mt-4 space-y-3 rounded-xl border border-brand-line bg-brand-bg-soft/50 p-3">
                    <div>
                      <label htmlFor={`mobile-document-category-${doc.id}`} className={labelClasses}>Category</label>
                      <select
                        id={`mobile-document-category-${doc.id}`}
                        value={editCategory}
                        onChange={(event) => setEditCategory(event.target.value)}
                        disabled={releaseLocked}
                        title={releaseLocked ? 'Assistant revision category is protected.' : undefined}
                        className={inputClasses}
                      >
                        <option value="">— none —</option>
                        {CATEGORIES.map((category) => <option key={category} value={category}>{category.charAt(0).toUpperCase() + category.slice(1)}</option>)}
                      </select>
                    </div>
                    <div>
                      <label htmlFor={`mobile-document-description-${doc.id}`} className={labelClasses}>Description</label>
                      <input
                        id={`mobile-document-description-${doc.id}`}
                        value={editDescription}
                        onChange={(event) => setEditDescription(event.target.value)}
                        className={inputClasses}
                      />
                    </div>
                    <div className="flex gap-2">
                      <button type="button" onClick={() => saveEdit(doc.id)} disabled={saving} className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-brand-ink px-3 text-sm font-bold text-white disabled:opacity-50">
                        <Check size={16} aria-hidden="true" /> Save
                      </button>
                      <button type="button" onClick={cancelEdit} className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-brand-line px-3 text-sm font-bold text-brand-ink">
                        <X size={16} aria-hidden="true" /> Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {doc.description && <p className="mt-3 text-sm leading-relaxed text-brand-ink-2">{doc.description}</p>}
                    <p className="mt-2 text-xs text-brand-muted">
                      Uploaded {doc.created_at ? (() => { try { return format(parseISO(doc.created_at), 'MMM d, yyyy') } catch { return doc.created_at } })() : 'date unavailable'}
                    </p>
                  </>
                )}

                <div className="mt-4 grid gap-2">
                  <button
                    type="button"
                    onClick={() => onReviseDocument?.(doc)}
                    disabled={!revisionAvailable}
                    aria-label={`Revise ${doc.filename} with assistant`}
                    aria-describedby={!revisionAvailable ? unsupportedReasonId : undefined}
                    title={revisionAvailable ? 'Revise with assistant' : 'Assistant revisions currently support DOCX files only.'}
                    className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-brand-ink px-4 text-sm font-bold text-white hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:bg-brand-bg-soft disabled:text-brand-muted"
                  >
                    <Sparkles size={16} aria-hidden="true" /> Revise with assistant
                  </button>
                  {!revisionAvailable && (
                    <p id={unsupportedReasonId} className="text-center text-[11px] leading-relaxed text-brand-muted">Assistant revisions currently support DOCX files only.</p>
                  )}
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2 border-t border-brand-line pt-3">
                  <button
                    type="button"
                    onClick={() => handleTogglePortalVisible(doc)}
                    disabled={releaseLocked}
                    aria-label={releaseLocked ? `${doc.filename} requires a separate release workflow` : doc.portal_visible ? `Make ${doc.filename} private` : `Share ${doc.filename} with client`}
                    aria-describedby={releaseLocked ? releaseLockedReasonId : undefined}
                    title={releaseLocked ? 'Assistant revisions require a separate destination approval workflow.' : undefined}
                    className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-brand-line px-3 text-xs font-bold text-brand-ink disabled:cursor-not-allowed disabled:bg-brand-bg-soft disabled:text-brand-muted"
                  >
                    {releaseLocked ? <ShieldCheck size={15} aria-hidden="true" /> : doc.portal_visible ? <Eye size={15} aria-hidden="true" /> : <EyeOff size={15} aria-hidden="true" />}
                    {releaseLocked ? 'Release locked' : doc.portal_visible ? 'Shared' : 'Private'}
                  </button>
                  <a
                    href={doc.cloud_url || getMatterDocumentDownloadUrl(matterId, doc.id)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-brand-line px-3 text-xs font-bold text-brand-ink"
                  >
                    {doc.cloud_url ? <ExternalLink size={15} aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}
                    {doc.cloud_url ? 'Open' : 'Download'}
                  </a>
                  <button type="button" onClick={() => startEdit(doc)} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-brand-line px-3 text-xs font-bold text-brand-ink">
                    <Pencil size={15} aria-hidden="true" /> Edit details
                  </button>
                  <button type="button" onClick={() => handleDelete(doc.id)} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-brand-rose/25 px-3 text-xs font-bold text-brand-rose">
                    <Trash2 size={15} aria-hidden="true" /> Delete
                  </button>
                  {releaseLocked && (
                    <p id={releaseLockedReasonId} className="col-span-2 text-center text-[11px] leading-relaxed text-brand-muted">Content approval does not authorize client release. Use a separate destination approval workflow.</p>
                  )}
                </div>
              </article>
            )
          })}
        </div>

        <div className="hidden bg-brand-surface border border-brand-line rounded-2xl overflow-x-auto shadow-sm md:block">
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
                  Folder &amp; Tags
                </th>
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Uploaded
                </th>
                <th className="text-left px-5 py-3.5 text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  Client Portal
                </th>
                <th className="px-5 py-3.5 text-right text-[11px] font-bold text-brand-muted uppercase tracking-widest">Actions</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr
                  key={doc.id}
                  draggable={editingId !== doc.id}
                  onDragStart={(event) => writeDraggedDocumentIds(event.dataTransfer, [doc.id])}
                  title="Drag onto a folder to file this document"
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
                          disabled={isAssistantRevisionDocument(doc)}
                          title={isAssistantRevisionDocument(doc) ? 'Assistant revision category is protected.' : undefined}
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
                      <td className="px-5 py-3" colSpan={3}>
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
                      <td className="relative px-5 py-3 align-top">
                        <div className="flex items-center gap-1.5 text-[12px] text-brand-muted">
                          <Folder size={12} aria-hidden="true" />
                          <span className="max-w-[140px] truncate" title={doc.folder_path || 'Unfiled'}>
                            {doc.folder_path || 'Unfiled'}
                          </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1">
                          {(doc.tags || []).map((tag) => (
                            <TagChip key={tag.id} tag={tag} />
                          ))}
                          <button
                            type="button"
                            onClick={() => setTagEditorDocId((current) => (current === doc.id ? null : doc.id))}
                            aria-label={`Edit tags for ${doc.filename}`}
                            aria-expanded={tagEditorDocId === doc.id}
                            className="inline-flex items-center gap-1 rounded-full border border-dashed border-brand-line px-2 py-0.5 text-[11px] font-sans text-brand-muted hover:border-brand-accent hover:text-brand-accent"
                          >
                            <TagIcon size={10} aria-hidden="true" />
                            {(doc.tags || []).length ? 'Edit' : 'Tag'}
                          </button>
                        </div>
                        {tagEditorDocId === doc.id && (
                          <DocumentTagEditor
                            documentId={doc.id}
                            documentTags={doc.tags || []}
                            tags={tags}
                            onApply={handleApplyTags}
                            onCreateTag={handleCreateTag}
                            onClose={() => setTagEditorDocId(null)}
                          />
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
                          disabled={isAssistantRevisionDocument(doc)}
                          aria-label={isAssistantRevisionDocument(doc) ? `${doc.filename} requires a separate release workflow` : doc.portal_visible ? 'Make private' : 'Share with client'}
                          title={isAssistantRevisionDocument(doc) ? 'Assistant revisions require a separate destination approval workflow.' : doc.portal_visible ? 'Make private' : 'Share with client'}
                          className="flex items-center gap-1.5 group disabled:cursor-not-allowed"
                        >
                          {isAssistantRevisionDocument(doc) ? (
                            <span className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                              <ShieldCheck size={11} /> Release locked
                            </span>
                          ) : doc.portal_visible ? (
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
                          <button
                            type="button"
                            onClick={() => onReviseDocument?.(doc)}
                            disabled={!canReviseWithAssistant(doc)}
                            aria-label={`Revise ${doc.filename} with assistant`}
                            title={canReviseWithAssistant(doc) ? 'Revise with assistant' : 'Assistant revisions currently support DOCX files only.'}
                            className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-brand-line px-2.5 text-xs font-bold text-brand-ink hover:border-brand-accent hover:bg-brand-bg-soft disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            <Sparkles size={15} aria-hidden="true" /> Revise
                          </button>
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
        </>
      )}
        </div>
      </div>
    </div>
  )
}
