import React, { useState, useEffect, useRef, useCallback } from 'react'
import { searchUsers } from '../api'

function Icon({ d, size = 16, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={d} />
    </svg>
  )
}

function initials(name, email) {
  if (name) {
    const parts = name.trim().split(/\s+/)
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    return parts[0].slice(0, 2).toUpperCase()
  }
  return email ? email.slice(0, 2).toUpperCase() : '??'
}

export default function UserSearchInput({ onSelect, placeholder = 'Search by name or email…', excludeIds = [] }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const timerRef = useRef(null)

  const doSearch = useCallback(async (q) => {
    if (q.length < 2) { setResults([]); setOpen(false); return }
    setLoading(true)
    try {
      const data = await searchUsers(q)
      const filtered = Array.isArray(data)
        ? data.filter(u => !excludeIds.includes(u.id))
        : []
      setResults(filtered)
      setOpen(true)
      setActiveIdx(-1)
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [excludeIds])

  useEffect(() => {
    clearTimeout(timerRef.current)
    if (query.length >= 2) {
      timerRef.current = setTimeout(() => doSearch(query), 300)
    } else {
      setResults([])
      setOpen(false)
    }
    return () => clearTimeout(timerRef.current)
  }, [query, doSearch])

  const handleSelect = (user) => {
    onSelect(user)
    setQuery('')
    setResults([])
    setOpen(false)
    setActiveIdx(-1)
  }

  const handleKeyDown = (e) => {
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIdx >= 0 && results[activeIdx]) handleSelect(results[activeIdx])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  useEffect(() => {
    if (activeIdx >= 0 && listRef.current) {
      const el = listRef.current.children[activeIdx]
      el?.scrollIntoView({ block: 'nearest' })
    }
  }, [activeIdx])

  return (
    <div className="relative">
      <div className="relative">
        <Icon
          d="M21 21l-6-6m2-5a7 7 0 1 1-14 0 7 7 0 0 1 14 0"
          size={15}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none"
        />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => query.length >= 2 && results.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder={placeholder}
          className="w-full border border-brand-line rounded-lg pl-9 pr-9 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all"
        />
        {loading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" />
        )}
      </div>

      {open && (
        <div
          ref={listRef}
          className="absolute z-50 mt-1 w-full bg-brand-surface border border-brand-line rounded-xl shadow-lg overflow-hidden max-h-52 overflow-y-auto"
        >
          {results.length === 0 ? (
            <div className="px-4 py-3 text-[13px] text-brand-muted font-sans">No users found</div>
          ) : (
            results.map((u, i) => (
              <button
                key={u.id}
                onMouseDown={() => handleSelect(u)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                  i === activeIdx ? 'bg-brand-accent/10' : 'hover:bg-brand-bg-soft'
                }`}
              >
                <div className="w-7 h-7 rounded-full bg-brand-accent/20 flex items-center justify-center text-[11px] font-bold text-brand-accent shrink-0 uppercase">
                  {initials(u.full_name, u.email)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-semibold text-brand-ink font-sans truncate">
                    {u.full_name || u.email}
                  </div>
                  {u.full_name && (
                    <div className="text-[11px] text-brand-muted font-sans truncate">{u.email}</div>
                  )}
                </div>
                <span className="text-[10px] font-bold uppercase text-brand-muted tracking-wide shrink-0">
                  {u.role}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
