import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  FileText,
  Download,
  Save,
  ChevronDown,
  ChevronUp,
  LoaderCircle,
  CheckCircle2,
  Check,
  Pencil,
} from 'lucide-react'
import { markdownComponents } from './legalMarkdown'
import {
  getArtifact,
  updateArtifact,
  exportArtifact,
} from '../api'
import SaveArtifactModal from './SaveArtifactModal'

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export default function ArtifactCard({ message, summary, conversationId, onSaved }) {
  const [expanded, setExpanded] = useState(false)
  const [artifact, setArtifact] = useState(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState('')
  const [showSaveModal, setShowSaveModal] = useState(false)
  const [savedState, setSavedState] = useState(summary.saved_to_matter)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')

  const loadArtifact = async () => {
    if (artifact) return artifact
    setBusy('load')
    setError('')
    try {
      const full = await getArtifact(conversationId, summary.id)
      setArtifact(full)
      setDraft(full.content)
      return full
    } catch (e) {
      setError('Could not load document content')
      return null
    } finally {
      setBusy('')
    }
  }

  const toggleExpanded = async () => {
    if (!expanded) await loadArtifact()
    setExpanded((v) => !v)
  }

  const handleSaveEdits = async () => {
    setBusy('edit')
    setError('')
    try {
      const updated = await updateArtifact(conversationId, summary.id, {
        content: draft,
      })
      setArtifact(updated)
      setEditing(false)
    } catch (e) {
      setError('Could not save edits')
    } finally {
      setBusy('')
    }
  }

  const handleExport = async (format) => {
    setBusy(format)
    setError('')
    try {
      const blob = await exportArtifact(conversationId, summary.id, format)
      const ext = format === 'markdown' ? 'md' : format
      downloadBlob(blob, `${summary.title.replace(/[^A-Za-z0-9._-]+/g, '-')}.${ext}`)
    } catch (e) {
      setError('Export failed')
    } finally {
      setBusy('')
    }
  }

  const handleSaved = (result) => {
    setSavedState(true)
    setWarning(result?.storage_warning || '')
    setShowSaveModal(false)
    if (onSaved) onSaved(result)
  }

  return (
    <div className="mt-4 border border-brand-gold/40 bg-brand-bg/60">
      <div className="flex items-center gap-2 border-b border-brand-line px-3 py-2">
        <FileText className="h-4 w-4 shrink-0 text-brand-gold" strokeWidth={2} />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-brand-ink">
          {summary.title}
        </span>
        {summary.version > 1 && (
          <span className="shrink-0 font-mono text-[10px] text-brand-muted">
            v{summary.version}
          </span>
        )}
        {savedState && (
          <span className="flex shrink-0 items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-brand-green">
            <CheckCircle2 className="h-3 w-3" /> Saved
          </span>
        )}
        <button
          onClick={toggleExpanded}
          className="shrink-0 p-1 text-brand-muted hover:text-brand-ink"
          title={expanded ? 'Collapse' : 'View document'}
        >
          {busy === 'load' ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : expanded ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </button>
      </div>

      {expanded && (
        <div className="px-3 py-2">
          {error && (
            <p className="mb-2 text-xs text-red-600">{error}</p>
          )}
          {warning && (
            <p className="mb-2 text-xs text-brand-amber">{warning}</p>
          )}
          {artifact && !editing && (
            <div className="max-h-96 overflow-y-auto text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {artifact.content}
              </ReactMarkdown>
            </div>
          )}
          {artifact && editing && (
            <textarea
              className="h-64 w-full border border-brand-line bg-brand-surface p-2 font-mono text-xs"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          )}
          {!artifact && busy !== 'load' && (
            <p className="text-xs text-brand-muted">No content loaded.</p>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-brand-line pt-2">
            {!editing && artifact && !savedState && (
              <button
                onClick={() => setEditing(true)}
                className="flex items-center gap-1 border border-brand-line px-2 py-1 text-xs text-brand-ink hover:bg-brand-surface"
              >
                <Pencil className="h-3 w-3" /> Edit
              </button>
            )}
            {editing && (
              <>
                <button
                  onClick={handleSaveEdits}
                  disabled={busy === 'edit'}
                  className="flex items-center gap-1 bg-brand-accent px-2 py-1 text-xs font-semibold text-white disabled:opacity-50"
                >
                  {busy === 'edit' ? (
                    <LoaderCircle className="h-3 w-3 animate-spin" />
                  ) : (
                    <Check className="h-3 w-3" />
                  )}
                  Save edits
                </button>
                <button
                  onClick={() => {
                    setEditing(false)
                    setDraft(artifact?.content || '')
                  }}
                  className="border border-brand-line px-2 py-1 text-xs text-brand-muted hover:bg-brand-surface"
                >
                  Cancel
                </button>
              </>
            )}

            {!savedState && !editing && (
              <button
                onClick={() => setShowSaveModal(true)}
                className="flex items-center gap-1 bg-brand-green px-2 py-1 text-xs font-semibold text-white hover:opacity-90"
              >
                <Save className="h-3 w-3" /> Save to matter
              </button>
            )}

            {!editing && (
              <div className="flex items-center gap-1">
                <Download className="h-3 w-3 text-brand-muted" />
                {['markdown', 'pdf', 'docx'].map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => handleExport(fmt)}
                    disabled={Boolean(busy)}
                    className="border border-brand-line px-2 py-1 text-[10px] font-mono uppercase text-brand-ink hover:bg-brand-surface disabled:opacity-50"
                  >
                    {busy === fmt ? '…' : fmt}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {showSaveModal && (
        <SaveArtifactModal
          conversationId={conversationId}
          artifactId={summary.id}
          defaultTitle={summary.title}
          defaultMatterId={artifact?.matter_id || message?.matter_id || ''}
          onClose={() => setShowSaveModal(false)}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}
