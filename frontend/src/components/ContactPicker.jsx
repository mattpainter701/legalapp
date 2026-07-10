import React, { useState, useCallback, useRef, useEffect } from 'react'
import { getContacts } from '../api'
import { User, Building2, X } from 'lucide-react'

export default function ContactPicker({ value, onChange, placeholder = 'Search contacts...', className = '', ariaLabel = 'Linked contact' }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState(value || null)
  const timeoutRef = useRef(null)
  const wrapperRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const search = useCallback((q) => {
    clearTimeout(timeoutRef.current)
    if (!q.trim()) {
      setResults([])
      return
    }
    timeoutRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await getContacts({ q, limit: 8 })
        setResults(data.items || [])
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 250)
  }, [])

  const handleInput = (e) => {
    const q = e.target.value
    setQuery(q)
    setOpen(true)
    search(q)
  }

  const handleSelect = (contact) => {
    setSelected(contact)
    setQuery('')
    setOpen(false)
    setResults([])
    onChange(contact)
  }

  const handleClear = () => {
    setSelected(null)
    setQuery('')
    onChange(null)
  }

  if (selected) {
    return (
      <div className={`flex items-center gap-2 px-3 py-2 border border-brand-line rounded bg-brand-bg-soft ${className}`}>
        {selected.entity_type === 'organization'
          ? <Building2 size={14} className="text-brand-muted shrink-0" />
          : <User size={14} className="text-brand-muted shrink-0" />
        }
        <span className="flex-1 text-sm text-brand-ink">{selected.display_name}</span>
        <span className="text-[11px] text-brand-muted">{selected.contact_type}</span>
        <button type="button" onClick={handleClear} aria-label={`Clear ${ariaLabel.toLowerCase()}`} className="text-brand-muted hover:text-brand-rose transition-colors">
          <X size={13} />
        </button>
      </div>
    )
  }

  return (
    <div ref={wrapperRef} className={`relative ${className}`}>
      <input
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-autocomplete="list"
        value={query}
        onChange={handleInput}
        onFocus={() => query && setOpen(true)}
        placeholder={placeholder}
        className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white text-brand-ink placeholder:text-brand-muted focus:outline-none focus:border-brand-accent"
      />
      {open && (loading || results.length > 0) && (
        <div role="listbox" aria-label={`${ariaLabel} results`} className="absolute z-50 top-full mt-1 w-full bg-white border border-brand-line rounded shadow-lg max-h-56 overflow-y-auto">
          {loading && (
            <div className="px-3 py-2 text-sm text-brand-muted">Searching...</div>
          )}
          {!loading && results.length === 0 && (
            <div className="px-3 py-2 text-sm text-brand-muted">No contacts found</div>
          )}
          {results.map((c) => (
            <button
              key={c.id}
              type="button"
              role="option"
              aria-selected="false"
              onClick={() => handleSelect(c)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-brand-bg-soft text-left transition-colors"
            >
              {c.entity_type === 'organization'
                ? <Building2 size={13} className="text-brand-muted shrink-0" />
                : <User size={13} className="text-brand-muted shrink-0" />
              }
              <span className="flex-1 text-sm text-brand-ink truncate">{c.display_name}</span>
              <span className="text-[11px] text-brand-muted capitalize shrink-0">
                {c.contact_type?.replace('_', ' ')}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
