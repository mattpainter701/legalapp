import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { getTemplate, getTemplates } from '../../api'

const RenderModal = lazy(() => import('../../pages/TemplatesPage').then(module => ({ default: module.RenderModal })))

export default function MatterTemplatePicker({ matterId, folderId, onClose, onSaved }) {
  const dialog = useRef(null)
  useEffect(() => { const previous = document.activeElement; return () => previous?.focus?.() }, [])
  const handleKey = event => {
    if (event.key === 'Escape' && !selecting) { event.stopPropagation(); onClose() }
    if (event.key === 'Tab') {
      const focusable = [...dialog.current.querySelectorAll('button:not([disabled]), input:not([disabled])')]; const first = focusable[0], last = focusable.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
  }
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const [selecting, setSelecting] = useState(false)
  useEffect(() => {
    let cancelled = false
    setLoading(true); setError('')
    const timer = setTimeout(() => {
      getTemplates({ query: query || undefined, include_inactive: false, limit: 20, offset: page * 20 })
        .then(data => { if (!cancelled) { setItems(data.items || []); setTotal(data.total || 0) } })
        .catch(() => { if (!cancelled) setError('Could not load the template library. Try again.') })
        .finally(() => { if (!cancelled) setLoading(false) })
    }, 150)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [query, page])
  async function choose(id) {
    setSelecting(true); setError('')
    try { setSelected(await getTemplate(id)) }
    catch { setError('Could not open this template. Try again.') }
    finally { setSelecting(false) }
  }
  if (selected) return <Suspense fallback={<p role="status">Opening document editor…</p>}><RenderModal key={`${matterId}:${selected.id}`} template={selected} fixedMatterId={matterId} folderId={folderId} onSaved={onSaved} onClose={onClose} /></Suspense>
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
    <section ref={dialog} onKeyDown={handleKey} role="dialog" aria-modal="true" aria-label="Attach template" className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-xl bg-brand-surface p-5 shadow-xl">
      <div className="flex items-center justify-between"><h2 className="text-xl font-semibold">Attach template</h2><button type="button" onClick={onClose} disabled={selecting}>Close</button></div>
      <p className="my-2 text-sm text-brand-muted">Choose a library template, complete its fields, and review the document before saving it to this matter.</p>
      <label className="block text-sm">Search templates<input autoFocus value={query} onChange={event => { setQuery(event.target.value); setPage(0) }} className="my-2 w-full rounded border border-brand-line p-2" /></label>
      {error && <p role="alert">{error}</p>}
      {loading ? <p role="status">Loading templates…</p> : <ul className="divide-y divide-brand-line">{items.map(template => <li key={template.id} className="flex items-center justify-between gap-3 py-3"><div><strong>{template.title}</strong><p className="text-xs text-brand-muted">{template.format || 'Text'} · {template.category || 'General'}</p></div><button type="button" disabled={selecting || !template.is_active} onClick={() => choose(template.id)} className="rounded border px-3 py-2">{template.is_active ? 'Select and preview' : 'Unavailable'}</button></li>)}</ul>}
      {!loading && !error && !items.length && <p>No matching active templates.</p>}
      <div className="mt-3 flex justify-between"><button type="button" disabled={loading || page === 0} onClick={() => setPage(page - 1)}>Previous</button><button type="button" disabled={loading || (page + 1) * 20 >= total} onClick={() => setPage(page + 1)}>Next</button></div>
    </section>
  </div>
}
