import React, { useState, useEffect, useCallback } from 'react'
import {
  getMatterSmbShares,
  addMatterSmbShare,
  removeMatterSmbShare,
  getSmbShares,
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
}

const inputCls =
  'w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
const labelCls =
  'block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5'

export default function MatterSmbSharesTab({ matterId }) {
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
              <label className={labelCls}>Share</label>
              <select
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
              <label className={labelCls}>Display Label</label>
              <input
                type="text"
                value={addForm.display_label}
                onChange={(e) => setAddForm((f) => ({ ...f, display_label: e.target.value }))}
                placeholder="Optional label…"
                className={inputCls}
              />
            </div>
            <div>
              <label className={labelCls}>Folder Path</label>
              <input
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
  )
}