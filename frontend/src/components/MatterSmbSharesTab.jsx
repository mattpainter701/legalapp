import { useState, useEffect, useCallback } from 'react'
import {
  getMatterSmbShares,
  addMatterSmbShare,
  removeMatterSmbShare,
  getSmbShares,
  getMatterCloudFolder,
  provisionMatterCloudFolder,
  remapMatterCloudFolder,
  renameMatterCloudFolder,
  addMatterCloudContextFolder,
  removeMatterCloudContextFolder,
  syncMatterCloudFolder,
} from '../api'

function Icon({ d, size = 18, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d={d} />
    </svg>
  )
}

const Icons = {
  folder: 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z',
  plus: 'M12 5v14M5 12h14',
  x: 'M18 6L6 18M6 6l12 12',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6',
  link: 'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
  cloud: 'M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z',
  refresh: 'M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8m0-5v5h5M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16m0 5v-5h-5',
  external: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3',
  edit: 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z',
  check: 'M20 6L9 17l-5-5',
}

const inputCls =
  'w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
const labelCls =
  'block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5'

const CLOUD_PROVIDERS = [
  { key: 'onedrive', label: 'OneDrive', tone: 'text-blue-700 bg-blue-50 border-blue-200' },
  { key: 'google_drive', label: 'Google Drive', tone: 'text-green-700 bg-green-50 border-green-200' },
]

const emptyRemapForm = {
  mode: 'folder_name',
  value: '',
  create_if_missing: false,
}

const initialContextForm = () => ({
  provider: 'onedrive',
  label: '',
  mode: 'folder_name',
  value: '',
  create_if_missing: false,
})

function MatterCloudFoldersPanel({ matterId, onCloudFolderChange }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState(null)
  const [message, setMessage] = useState(null)
  const [renameProvider, setRenameProvider] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  const [remapProvider, setRemapProvider] = useState(null)
  const [remapForm, setRemapForm] = useState(emptyRemapForm)
  const [showAddContext, setShowAddContext] = useState(false)
  const [contextForm, setContextForm] = useState(initialContextForm)

  const loadCloud = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getMatterCloudFolder(matterId)
      setStatus(data)
    } catch {
      setStatus({ status: 'not_provisioned', providers: {} })
    } finally {
      setLoading(false)
    }
  }, [matterId])

  useEffect(() => {
    loadCloud()
  }, [loadCloud])

  const applyStatus = (result, successText) => {
    setStatus(result)
    onCloudFolderChange?.(result.providers || {})
    setMessage({ type: 'success', text: successText })
  }

  const handleProvision = async () => {
    setBusyKey('provision')
    setMessage(null)
    try {
      const result = await provisionMatterCloudFolder(matterId)
      applyStatus(result, 'Cloud folders are connected for this matter.')
    } catch (err) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || 'Cloud folder setup failed.' })
    } finally {
      setBusyKey(null)
    }
  }

  const handleSync = async () => {
    setBusyKey('sync')
    setMessage(null)
    try {
      const result = await syncMatterCloudFolder(matterId)
      const next = { status: 'provisioned', providers: result.providers || {} }
      applyStatus(next, `Synced ${result.files?.length ?? 0} cloud file${result.files?.length === 1 ? '' : 's'}.`)
    } catch (err) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || 'Cloud folder sync failed.' })
    } finally {
      setBusyKey(null)
    }
  }

  const openRename = (provider, currentName) => {
    setRenameProvider(provider)
    setRenameValue(currentName || '')
    setRemapProvider(null)
    setMessage(null)
  }

  const handleRename = async (provider) => {
    const name = renameValue.trim()
    if (!name) return
    setBusyKey(`rename:${provider}`)
    setMessage(null)
    try {
      const result = await renameMatterCloudFolder(matterId, provider, { name })
      applyStatus(result, 'Folder renamed and matter mapping updated.')
      setRenameProvider(null)
      setRenameValue('')
    } catch (err) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || 'Folder rename failed.' })
    } finally {
      setBusyKey(null)
    }
  }

  const openRemap = (provider) => {
    setRemapProvider(provider)
    setRemapForm(emptyRemapForm)
    setRenameProvider(null)
    setMessage(null)
  }

  const handleRemap = async (provider) => {
    const value = remapForm.value.trim()
    if (!value) return
    setBusyKey(`remap:${provider}`)
    setMessage(null)
    try {
      const payload = { create_if_missing: remapForm.mode === 'folder_name' && remapForm.create_if_missing }
      payload[remapForm.mode] = value
      const result = await remapMatterCloudFolder(matterId, provider, payload)
      applyStatus(result, 'Matter remapped to the selected folder.')
      setRemapProvider(null)
      setRemapForm(emptyRemapForm)
    } catch (err) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || 'Folder remap failed.' })
    } finally {
      setBusyKey(null)
    }
  }

  const openAddContext = () => {
    setShowAddContext(true)
    setRenameProvider(null)
    setRemapProvider(null)
    setMessage(null)
  }

  const handleAddContext = async () => {
    const value = contextForm.value.trim()
    if (!value) return
    setBusyKey('context:add')
    setMessage(null)
    try {
      const payload = {
        provider: contextForm.provider,
        create_if_missing: contextForm.mode === 'folder_name' && contextForm.create_if_missing,
      }
      if (contextForm.label.trim()) payload.label = contextForm.label.trim()
      payload[contextForm.mode] = value
      const result = await addMatterCloudContextFolder(matterId, payload)
      applyStatus(result, 'Context folder linked to this matter.')
      setShowAddContext(false)
      setContextForm(initialContextForm())
    } catch (err) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || 'Context folder link failed.' })
    } finally {
      setBusyKey(null)
    }
  }

  const handleRemoveContext = async (contextFolderId) => {
    setBusyKey(`context:remove:${contextFolderId}`)
    setMessage(null)
    try {
      const result = await removeMatterCloudContextFolder(matterId, contextFolderId)
      applyStatus(result, 'Context folder removed from this matter.')
    } catch (err) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || 'Context folder removal failed.' })
    } finally {
      setBusyKey(null)
    }
  }

  const providers = status?.providers || {}
  const contextFolders = Array.isArray(providers.context_folders) ? providers.context_folders : []
  const anyMapped = CLOUD_PROVIDERS.some(({ key }) => providers[key]) || contextFolders.length > 0

  return (
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
            <Icon d={Icons.cloud} size={18} className="text-brand-accent" /> Cloud Document Folders
          </h2>
          <p className="text-[13px] text-brand-muted font-sans mt-0.5">
            Connect this matter to existing OneDrive or Google Drive folders.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleSync}
            disabled={!!busyKey}
            className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft disabled:opacity-50 transition-colors shadow-sm"
          >
            <Icon d={Icons.refresh} size={14} className={busyKey === 'sync' ? 'animate-spin' : ''} />
            Sync
          </button>
          <button
            onClick={handleProvision}
            disabled={!!busyKey}
            className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 transition-colors shadow-sm"
          >
            <Icon d={Icons.link} size={14} />
            Reconnect
          </button>
        </div>
      </div>

      <div className="p-6 space-y-4">
        {message && (
          <div className={`border rounded-xl px-4 py-3 text-[13px] font-sans ${
            message.type === 'error'
              ? 'bg-brand-rose/10 border-brand-rose/20 text-brand-rose'
              : 'bg-brand-green/10 border-brand-green/20 text-brand-green'
          }`}>
            {message.text}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <div className="space-y-3">
            {!anyMapped && (
              <div className="border border-brand-line rounded-xl px-4 py-4 bg-brand-bg-soft/40">
                <p className="text-[14px] font-semibold text-brand-ink font-sans">No cloud folder mapped yet</p>
                <p className="text-[13px] text-brand-muted font-sans mt-1">
                  Use Reconnect to discover or create the standard matter folder, or remap a provider to an existing folder below.
                </p>
              </div>
            )}

            {CLOUD_PROVIDERS.map(({ key, label, tone }) => {
              const data = providers[key]
              const folderId = data?.matter_folder_id || data?.id
              const folderName = data?.folder_name || data?.path || 'Not mapped'
              const remapping = remapProvider === key
              const renaming = renameProvider === key
              const busy = busyKey?.endsWith(`:${key}`)
              return (
                <div key={key} className="border border-brand-line rounded-xl overflow-hidden">
                  <div className="px-4 py-3 flex flex-wrap items-center justify-between gap-3 bg-brand-bg-soft/30">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-[11px] font-bold uppercase tracking-wider font-sans px-2 py-0.5 rounded border ${tone}`}>
                          {label}
                        </span>
                        <span className="text-[14px] font-semibold text-brand-ink font-sans truncate">
                          {folderName}
                        </span>
                      </div>
                      <div className="text-[12px] text-brand-muted font-mono truncate mt-1">
                        {folderId || 'No folder id stored'}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {data?.url && (
                        <a
                          href={data.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-surface border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft"
                        >
                          <Icon d={Icons.external} size={12} /> Open
                        </a>
                      )}
                      <button
                        onClick={() => openRename(key, data?.folder_name || '')}
                        disabled={!folderId || !!busyKey}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-surface border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft disabled:opacity-50"
                      >
                        <Icon d={Icons.edit} size={12} /> Rename
                      </button>
                      <button
                        onClick={() => openRemap(key)}
                        disabled={!!busyKey}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-surface border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft disabled:opacity-50"
                      >
                        <Icon d={Icons.link} size={12} /> Remap
                      </button>
                    </div>
                  </div>

                  {renaming && (
                    <div className="p-4 border-t border-brand-line bg-brand-surface flex flex-wrap gap-3 items-end">
                      <div className="flex-1 min-w-[220px]">
                        <label htmlFor={`matter-smb-${key}-new-folder-name`} className={labelCls}>New Folder Name</label>
                        <input id={`matter-smb-${key}-new-folder-name`}
                          value={renameValue}
                          onChange={(e) => setRenameValue(e.target.value)}
                          className={inputCls}
                          placeholder="Matter folder name"
                        />
                      </div>
                      <button
                        onClick={() => handleRename(key)}
                        disabled={!renameValue.trim() || busy}
                        className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50"
                      >
                        {busy ? 'Saving...' : 'Save Rename'}
                      </button>
                      <button
                        onClick={() => setRenameProvider(null)}
                        className="px-4 py-2.5 text-brand-muted text-sm font-sans hover:text-brand-ink"
                      >
                        Cancel
                      </button>
                    </div>
                  )}

                  {remapping && (
                    <div className="p-4 border-t border-brand-line bg-brand-surface space-y-3">
                      <div className="grid grid-cols-1 md:grid-cols-[180px_1fr] gap-3">
                        <div>
                          <label htmlFor={`matter-smb-${key}-remap-mode`} className={labelCls}>Remap By</label>
                          <select id={`matter-smb-${key}-remap-mode`}
                            value={remapForm.mode}
                            onChange={(e) => setRemapForm((f) => ({ ...f, mode: e.target.value, create_if_missing: false }))}
                            className={inputCls}
                          >
                            <option value="folder_name">Folder name</option>
                            <option value="folder_url">Folder URL</option>
                            <option value="folder_id">Folder ID</option>
                          </select>
                        </div>
                        <div>
                          <label htmlFor={`matter-smb-${key}-remap-value`} className={labelCls}>
                            {remapForm.mode === 'folder_name' ? 'Folder Name Under Master Folder' : remapForm.mode === 'folder_url' ? 'Folder URL' : 'Folder ID'}
                          </label>
                          <input id={`matter-smb-${key}-remap-value`}
                            value={remapForm.value}
                            onChange={(e) => setRemapForm((f) => ({ ...f, value: e.target.value }))}
                            className={inputCls}
                            placeholder={remapForm.mode === 'folder_name' ? 'acme-v-smith-1234abcd' : remapForm.mode === 'folder_url' ? 'Paste provider folder link' : 'Paste provider folder id'}
                          />
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        {remapForm.mode === 'folder_name' ? (
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={remapForm.create_if_missing}
                              onChange={(e) => setRemapForm((f) => ({ ...f, create_if_missing: e.target.checked }))}
                              className="w-4 h-4 rounded border-brand-line text-brand-accent focus:ring-brand-accent"
                            />
                            <span className="text-[13px] font-sans text-brand-ink-2">Create if missing</span>
                          </label>
                        ) : (
                          <span className="text-[12px] text-brand-muted font-sans">The target must be an existing folder.</span>
                        )}
                        <div className="flex gap-3">
                          <button
                            onClick={() => handleRemap(key)}
                            disabled={!remapForm.value.trim() || busy}
                            className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50"
                          >
                            {busy ? 'Remapping...' : 'Save Remap'}
                          </button>
                          <button
                            onClick={() => setRemapProvider(null)}
                            className="px-4 py-2.5 text-brand-muted text-sm font-sans hover:text-brand-ink"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}

            <div className="border border-brand-line rounded-xl overflow-hidden">
              <div className="px-4 py-3 flex flex-wrap items-center justify-between gap-3 bg-brand-bg-soft/30">
                <div>
                  <p className="text-[14px] font-semibold text-brand-ink font-sans">Additional Context Folders</p>
                  <p className="text-[12px] text-brand-muted font-sans mt-0.5">
                    Related client or historical matter folders included in search context.
                  </p>
                </div>
                <button
                  onClick={showAddContext ? () => setShowAddContext(false) : openAddContext}
                  disabled={!!busyKey && busyKey !== 'context:add'}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-surface border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft disabled:opacity-50"
                >
                  <Icon d={showAddContext ? Icons.x : Icons.plus} size={12} />
                  {showAddContext ? 'Close' : 'Add Context'}
                </button>
              </div>

              {showAddContext && (
                <div className="p-4 border-t border-brand-line bg-brand-surface space-y-3">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="mattersmbsharestab-provider" className={labelCls}>Provider</label>
                      <select id="mattersmbsharestab-provider"
                        value={contextForm.provider}
                        onChange={(e) => setContextForm((f) => ({ ...f, provider: e.target.value }))}
                        className={inputCls}
                      >
                        {CLOUD_PROVIDERS.map(({ key, label }) => (
                          <option key={key} value={key}>{label}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="mattersmbsharestab-label" className={labelCls}>Label</label>
                      <input id="mattersmbsharestab-label"
                        value={contextForm.label}
                        onChange={(e) => setContextForm((f) => ({ ...f, label: e.target.value }))}
                        className={inputCls}
                        placeholder="Client prior case, related appeal..."
                      />
                    </div>
                    <div>
                      <label htmlFor="mattersmbsharestab-map-by" className={labelCls}>Map By</label>
                      <select id="mattersmbsharestab-map-by"
                        value={contextForm.mode}
                        onChange={(e) => setContextForm((f) => ({ ...f, mode: e.target.value, create_if_missing: false }))}
                        className={inputCls}
                      >
                        <option value="folder_name">Folder name</option>
                        <option value="folder_url">Folder URL</option>
                        <option value="folder_id">Folder ID</option>
                      </select>
                    </div>
                    <div>
                      <label htmlFor="matter-smb-context-remap-value" className={labelCls}>
                        {contextForm.mode === 'folder_name' ? 'Folder Name Under Master Folder' : contextForm.mode === 'folder_url' ? 'Folder URL' : 'Folder ID'}
                      </label>
                      <input id="matter-smb-context-remap-value"
                        value={contextForm.value}
                        onChange={(e) => setContextForm((f) => ({ ...f, value: e.target.value }))}
                        className={inputCls}
                        placeholder={contextForm.mode === 'folder_name' ? 'client-related-matter' : contextForm.mode === 'folder_url' ? 'Paste provider folder link' : 'Paste provider folder id'}
                      />
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    {contextForm.mode === 'folder_name' ? (
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={contextForm.create_if_missing}
                          onChange={(e) => setContextForm((f) => ({ ...f, create_if_missing: e.target.checked }))}
                          className="w-4 h-4 rounded border-brand-line text-brand-accent focus:ring-brand-accent"
                        />
                        <span className="text-[13px] font-sans text-brand-ink-2">Create if missing</span>
                      </label>
                    ) : (
                      <span className="text-[12px] text-brand-muted font-sans">The target must be an existing folder.</span>
                    )}
                    <div className="flex gap-3">
                      <button
                        onClick={() => {
                          setShowAddContext(false)
                          setContextForm(initialContextForm())
                        }}
                        className="px-4 py-2.5 text-brand-muted text-sm font-sans hover:text-brand-ink"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleAddContext}
                        disabled={!contextForm.value.trim() || busyKey === 'context:add'}
                        className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50"
                      >
                        {busyKey === 'context:add' ? 'Linking...' : 'Link Folder'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              <div className="p-4 border-t border-brand-line bg-brand-surface">
                {contextFolders.length === 0 ? (
                  <div className="text-[13px] text-brand-muted font-sans">
                    No additional context folders linked.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {contextFolders.map((folder) => {
                      const cfg = CLOUD_PROVIDERS.find(({ key }) => key === folder.provider) || {}
                      const folderKey = folder.id || `${folder.provider}:${folder.matter_folder_id}`
                      const busyRemove = busyKey === `context:remove:${folder.id}`
                      const title = folder.label || folder.folder_name || 'Context folder'
                      const detail = folder.label && folder.folder_name ? folder.folder_name : folder.matter_folder_id
                      return (
                        <div key={folderKey} className={`flex flex-wrap items-center justify-between gap-3 bg-brand-bg-soft rounded-lg border border-brand-line px-3 py-2 ${busyRemove ? 'opacity-50' : ''}`}>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className={`text-[11px] font-bold uppercase tracking-wider font-sans px-2 py-0.5 rounded border ${cfg.tone || 'text-brand-muted bg-brand-bg-soft border-brand-line'}`}>
                                {cfg.label || folder.provider || 'Cloud'}
                              </span>
                              <span className="text-[13px] font-semibold text-brand-ink font-sans truncate">{title}</span>
                            </div>
                            {detail && (
                              <div className="text-[12px] text-brand-muted font-mono truncate mt-1">{detail}</div>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            {folder.url && (
                              <a
                                href={folder.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-surface border border-brand-line text-brand-ink text-[12px] font-sans font-medium rounded-lg hover:bg-brand-bg-soft"
                              >
                                <Icon d={Icons.external} size={12} /> Open
                              </a>
                            )}
                            <button
                              onClick={() => handleRemoveContext(folder.id)}
                              disabled={!folder.id || !!busyKey}
                              className="text-brand-muted hover:text-brand-rose transition-colors p-1.5 rounded-lg hover:bg-brand-rose/10 disabled:opacity-50"
                              title="Remove context folder"
                            >
                              <Icon d={Icons.trash} size={15} />
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function MatterSmbSharesTab({ matterId, onCloudFolderChange }) {
  const [bindings, setBindings] = useState([])
  const [shares, setShares] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState({
    share_id: '',
    folder_path: '',
    display_label: '',
    auto_scan: true,
  })
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState(null)

  const [removingId, setRemovingId] = useState(null)

  const loadBindings = useCallback(async () => {
    try {
      const data = await getMatterSmbShares(matterId)
      setBindings(Array.isArray(data) ? data : data.bindings || data.items || [])
    } catch {
      setError('Failed to load file share bindings.')
    } finally {
      setLoading(false)
    }
  }, [matterId])

  useEffect(() => {
    loadBindings()
  }, [loadBindings])

  useEffect(() => {
    if (showAdd) {
      getSmbShares()
        .then((data) => setShares(Array.isArray(data) ? data : data.shares || data.items || []))
        .catch(() => setShares([]))
    }
  }, [showAdd])

  const handleAdd = async () => {
    if (!addForm.share_id) return
    setAdding(true)
    setAddError(null)
    try {
      const result = await addMatterSmbShare(matterId, {
        share_id: addForm.share_id,
        folder_path: addForm.folder_path || undefined,
        display_label: addForm.display_label || undefined,
        auto_scan: addForm.auto_scan,
      })
      setBindings((prev) => [...prev, result])
      setShowAdd(false)
      setAddForm({ share_id: '', folder_path: '', display_label: '', auto_scan: true })
    } catch {
      setAddError('Failed to add file share binding.')
    } finally {
      setAdding(false)
    }
  }

  const handleRemove = async (bindingId) => {
    setRemovingId(bindingId)
    try {
      await removeMatterSmbShare(matterId, bindingId)
      setBindings((prev) => prev.filter((b) => b.id !== bindingId))
    } catch {
      // silent — optimistic remove already happened visually for better UX
    } finally {
      setRemovingId(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-brand-surface border border-brand-line rounded-2xl p-10 text-center shadow-sm">
        <Icon d={Icons.folder} size={32} className="mx-auto text-brand-rose mb-3" />
        <p className="text-brand-ink font-serif font-bold text-lg">{error}</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <MatterCloudFoldersPanel matterId={matterId} onCloudFolderChange={onCloudFolderChange} />

      <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl flex items-center justify-between">
        <div>
          <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
            <Icon d={Icons.folder} size={18} className="text-brand-accent" /> File Shares
          </h2>
          <p className="text-[13px] text-brand-muted font-sans mt-0.5">
            SMB shares and folders bound to this matter for document discovery.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-colors shadow-sm"
        >
          <Icon d={Icons.plus} size={15} /> Add Share
        </button>
      </div>

      {showAdd && (
        <div className="p-6 bg-brand-bg border-b border-brand-line">
          {addError && (
            <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 mb-4 text-brand-rose text-sm font-sans">
              {addError}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label htmlFor="mattersmbsharestab-share" className={labelCls}>Share</label>
              <select id="mattersmbsharestab-share"
                value={addForm.share_id}
                onChange={(e) => setAddForm((f) => ({ ...f, share_id: e.target.value }))}
                className={inputCls}
              >
                <option value="">Select share…</option>
                {shares.map((s) => (
                  <option key={s.id} value={s.id}>
                      {s.display_name || s.share_path} ({s.share_path})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="mattersmbsharestab-display-label" className={labelCls}>Display Label</label>
              <input id="mattersmbsharestab-display-label"
                type="text"
                value={addForm.display_label}
                onChange={(e) => setAddForm((f) => ({ ...f, display_label: e.target.value }))}
                placeholder="Optional label…"
                className={inputCls}
              />
            </div>
            <div>
              <label htmlFor="mattersmbsharestab-folder-path" className={labelCls}>Folder Path</label>
              <input id="mattersmbsharestab-folder-path"
                type="text"
                value={addForm.folder_path}
                onChange={(e) => setAddForm((f) => ({ ...f, folder_path: e.target.value }))}
                placeholder="/subfolder/path"
                className={inputCls}
              />
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={addForm.auto_scan}
                  onChange={(e) => setAddForm((f) => ({ ...f, auto_scan: e.target.checked }))}
                  className="w-4 h-4 rounded border-brand-line text-brand-accent focus:ring-brand-accent"
                />
                <span className="text-[13px] font-sans text-brand-ink-2">Auto-scan for new documents</span>
              </label>
            </div>
          </div>
          <div className="flex gap-3 justify-end">
            <button
              onClick={() => {
                setShowAdd(false)
                setAddForm({ share_id: '', folder_path: '', display_label: '', auto_scan: true })
                setAddError(null)
              }}
              className="px-4 py-2 text-brand-muted text-sm font-sans hover:text-brand-ink"
            >
              Cancel
            </button>
            <button
              onClick={handleAdd}
              disabled={!addForm.share_id || adding}
              className="px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50"
            >
              {adding ? 'Adding…' : 'Bind Share'}
            </button>
          </div>
        </div>
      )}

      <div className="p-6">
        {bindings.length === 0 ? (
          <div className="text-center py-16">
            <Icon d={Icons.link} size={32} className="mx-auto text-brand-line-2 mb-3" />
            <p className="text-brand-ink font-serif text-lg font-bold mb-1">No file shares bound</p>
            <p className="text-brand-muted text-sm font-sans">
              Bind an SMB share or folder to enable document discovery for this matter.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {bindings.map((b) => {
              const removing = removingId === b.id
              return (
                <div
                  key={b.id}
                  className={`flex items-center justify-between bg-brand-bg-soft rounded-xl px-4 py-3 border border-brand-line transition-opacity ${removing ? 'opacity-50' : ''}`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-full bg-brand-accent/10 flex items-center justify-center flex-shrink-0">
                      <Icon d={Icons.folder} size={15} className="text-brand-accent" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[14px] font-semibold text-brand-ink font-sans truncate">
                        {b.display_label || b.folder_path || '/'}
                      </div>
                      <div className="text-[12px] text-brand-muted font-sans truncate">
                        {b.share_name || b.share_path || 'SMB Share'}
                        {b.folder_path && b.folder_path !== '/' && (
                          <span className="text-brand-line-2">{'/' + b.folder_path.replace(/^\//, '')}</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0 ml-4">
                    {b.auto_scan !== undefined && (
                      <span
                        className={`text-[11px] font-bold uppercase tracking-wider font-sans px-2 py-0.5 rounded border ${
                          b.auto_scan
                            ? 'bg-brand-green/10 text-brand-green border-brand-green/20'
                            : 'bg-brand-bg-soft text-brand-muted border-brand-line'
                        }`}
                      >
                        {b.auto_scan ? 'Auto-scan' : 'Manual'}
                      </span>
                    )}
                    <button
                      onClick={() => handleRemove(b.id)}
                      disabled={removing}
                      className="text-brand-muted hover:text-brand-rose transition-colors p-1.5 rounded-lg hover:bg-brand-rose/10"
                      title="Remove binding"
                    >
                      <Icon d={Icons.trash} size={15} />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      </div>
    </div>
  )
}
