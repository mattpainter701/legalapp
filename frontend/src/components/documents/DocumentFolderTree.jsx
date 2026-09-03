import { useCallback, useMemo, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Files,
  Folder,
  FolderOpen,
  FolderPlus,
  Inbox,
  Lock,
  Pencil,
  Trash2,
} from 'lucide-react'
import { ALL_DOCUMENTS, ROOT_FOLDER } from '../../hooks/useMatterDocumentExplorer'

const DOCUMENT_DRAG_TYPE = 'application/x-lawhand-documents'

/** Read dragged document ids, tolerating browsers that only expose text/plain. */
export function readDraggedDocumentIds(dataTransfer) {
  const raw =
    dataTransfer?.getData?.(DOCUMENT_DRAG_TYPE) ||
    dataTransfer?.getData?.('text/plain') ||
    ''
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(Boolean) : []
  } catch {
    return []
  }
}

export function writeDraggedDocumentIds(dataTransfer, documentIds) {
  const payload = JSON.stringify(documentIds)
  dataTransfer.setData(DOCUMENT_DRAG_TYPE, payload)
  dataTransfer.setData('text/plain', payload)
  dataTransfer.effectAllowed = 'move'
}

function FolderRow({
  label,
  count,
  icon,
  depth = 0,
  selected,
  expandable = false,
  expanded = false,
  onToggle,
  onSelect,
  onDropDocuments,
  actions = null,
  dropDisabled = false,
}) {
  const [dragOver, setDragOver] = useState(false)

  const handleDragOver = (event) => {
    if (dropDisabled || !onDropDocuments) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDragOver(true)
  }

  const handleDrop = (event) => {
    if (dropDisabled || !onDropDocuments) return
    event.preventDefault()
    setDragOver(false)
    const ids = readDraggedDocumentIds(event.dataTransfer)
    if (ids.length) onDropDocuments(ids)
  }

  return (
    <div
      className={`group flex items-center gap-1 rounded-lg pr-1 transition-colors ${
        selected ? 'bg-brand-bg-soft' : 'hover:bg-brand-bg-soft/60'
      } ${dragOver ? 'ring-2 ring-brand-accent ring-offset-1' : ''}`}
      style={{ paddingLeft: `${depth * 12}px` }}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {expandable ? (
        <button
          type="button"
          onClick={onToggle}
          aria-label={expanded ? `Collapse ${label}` : `Expand ${label}`}
          aria-expanded={expanded}
          className="shrink-0 rounded p-0.5 text-brand-muted hover:text-brand-ink"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
      ) : (
        <span className="w-[22px] shrink-0" aria-hidden="true" />
      )}
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        className={`flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-lg py-1.5 pr-2 text-left text-[13px] font-sans ${
          selected ? 'font-bold text-brand-ink' : 'text-brand-ink-2'
        }`}
      >
        <span className="shrink-0 text-brand-accent">{icon}</span>
        <span className="truncate">{label}</span>
      </button>
      {/* Outside the button so the folder's accessible name stays the folder
          name, not "Discovery 4". */}
      {typeof count === 'number' && (
        <span
          aria-hidden="true"
          className="ml-auto shrink-0 pl-2 text-[11px] tabular-nums text-brand-muted"
        >
          {count}
        </span>
      )}
      {actions && (
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          {actions}
        </span>
      )}
    </div>
  )
}

/**
 * Finder-style folder rail for a matter's documents.
 *
 * Folders are also drop targets: dragging document rows onto one files them
 * there, which is the fastest way to organize an existing flat pile.
 */
export default function DocumentFolderTree({
  folders,
  foldersByParent,
  rootDocumentCount,
  totalDocumentCount,
  selectedFolderId,
  onSelectFolder,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onDropDocuments,
}) {
  const [expanded, setExpanded] = useState(() => new Set())

  const toggle = useCallback((id) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Auto-reveal the ancestors of the selected folder so a deep selection is
  // never hidden inside a collapsed branch.
  const revealed = useMemo(() => {
    const set = new Set(expanded)
    const byId = new Map(folders.map((f) => [f.id, f]))
    let node = byId.get(selectedFolderId)
    while (node?.parent_id) {
      set.add(node.parent_id)
      node = byId.get(node.parent_id)
    }
    return set
  }, [expanded, folders, selectedFolderId])

  const renderBranch = (parentKey, depth) => {
    const children = foldersByParent.get(parentKey) || []
    return children.map((folder) => {
      const hasChildren = (foldersByParent.get(folder.id) || []).length > 0
      const isExpanded = revealed.has(folder.id)
      const isSystem = folder.kind === 'system'
      return (
        <div key={folder.id}>
          <FolderRow
            label={folder.name}
            count={folder.document_count}
            depth={depth}
            selected={selectedFolderId === folder.id}
            expandable={hasChildren}
            expanded={isExpanded}
            onToggle={() => toggle(folder.id)}
            onSelect={() => onSelectFolder(folder.id)}
            onDropDocuments={(ids) => onDropDocuments(ids, folder.id)}
            icon={
              isSystem ? (
                <Lock size={14} aria-hidden="true" />
              ) : selectedFolderId === folder.id ? (
                <FolderOpen size={15} aria-hidden="true" />
              ) : (
                <Folder size={15} aria-hidden="true" />
              )
            }
            actions={
              <>
                <button
                  type="button"
                  onClick={() => onCreateFolder(folder.id)}
                  aria-label={`New subfolder in ${folder.name}`}
                  title="New subfolder"
                  className="rounded p-1 text-brand-muted hover:bg-brand-surface hover:text-brand-ink"
                >
                  <FolderPlus size={13} />
                </button>
                {!isSystem && (
                  <>
                    <button
                      type="button"
                      onClick={() => onRenameFolder(folder)}
                      aria-label={`Rename ${folder.name}`}
                      title="Rename folder"
                      className="rounded p-1 text-brand-muted hover:bg-brand-surface hover:text-brand-ink"
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeleteFolder(folder)}
                      aria-label={`Delete ${folder.name}`}
                      title="Delete folder"
                      className="rounded p-1 text-brand-muted hover:bg-brand-surface hover:text-brand-rose"
                    >
                      <Trash2 size={13} />
                    </button>
                  </>
                )}
              </>
            }
          />
          {isExpanded && renderBranch(folder.id, depth + 1)}
        </div>
      )
    })
  }

  return (
    <nav aria-label="Document folders" className="space-y-0.5">
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="text-[11px] font-bold uppercase tracking-widest text-brand-muted">
          Folders
        </h3>
        <button
          type="button"
          onClick={() => onCreateFolder(null)}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-[12px] font-sans font-medium text-brand-ink hover:bg-brand-bg-soft"
        >
          <FolderPlus size={13} /> New
        </button>
      </div>

      <FolderRow
        label="All documents"
        count={totalDocumentCount}
        selected={selectedFolderId === ALL_DOCUMENTS}
        onSelect={() => onSelectFolder(ALL_DOCUMENTS)}
        icon={<Files size={15} aria-hidden="true" />}
        dropDisabled
      />
      <FolderRow
        label="Unfiled"
        count={rootDocumentCount}
        selected={selectedFolderId === ROOT_FOLDER}
        onSelect={() => onSelectFolder(ROOT_FOLDER)}
        onDropDocuments={(ids) => onDropDocuments(ids, ROOT_FOLDER)}
        icon={<Inbox size={15} aria-hidden="true" />}
      />

      {folders.length > 0 && <div className="my-2 border-t border-brand-line/60" />}
      {renderBranch(ROOT_FOLDER, 0)}

      {folders.length === 0 && (
        <p className="px-2 py-3 text-[12px] leading-relaxed text-brand-muted">
          No folders yet. Create one to group pleadings, discovery, or
          correspondence, then drag documents onto it.
        </p>
      )}
    </nav>
  )
}
