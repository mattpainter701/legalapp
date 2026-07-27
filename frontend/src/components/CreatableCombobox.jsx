import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ChevronDown, Plus } from 'lucide-react'

const uniqueOptions = (options) => {
  const seen = new Set()
  return (options || []).filter((rawOption) => {
    const option = String(rawOption || '').trim()
    const key = option.toLocaleLowerCase()
    if (!option || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export default function CreatableCombobox({
  id,
  value,
  onChange,
  options = [],
  placeholder = 'Select or type a new value',
  className = '',
  required = false,
  disabled = false,
  maxOptions = 12,
}) {
  const generatedId = useId()
  const inputId = id || `creatable-combobox-${generatedId}`
  const listboxId = `${inputId}-options`
  const wrapperRef = useRef(null)
  const inputRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [filtering, setFiltering] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)

  const normalizedOptions = useMemo(() => uniqueOptions(options), [options])
  const query = String(value || '').trim()
  const matchingOptions = useMemo(() => {
    const filtered = filtering && query
      ? normalizedOptions.filter(option => option.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
      : normalizedOptions
    return filtered.slice(0, maxOptions)
  }, [filtering, maxOptions, normalizedOptions, query])
  const hasExactMatch = normalizedOptions.some(option => option.toLocaleLowerCase() === query.toLocaleLowerCase())
  const canUseNewValue = filtering && Boolean(query) && !hasExactMatch
  const choices = [
    ...matchingOptions.map(option => ({ type: 'option', value: option })),
    ...(canUseNewValue ? [{ type: 'new', value: query }] : []),
  ]

  useEffect(() => {
    const closeOnOutsideClick = (event) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setOpen(false)
        setActiveIndex(-1)
      }
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => document.removeEventListener('mousedown', closeOnOutsideClick)
  }, [])

  useEffect(() => {
    if (activeIndex >= choices.length) setActiveIndex(choices.length - 1)
  }, [activeIndex, choices.length])

  const openAllOptions = () => {
    if (disabled) return
    setFiltering(false)
    setOpen(true)
    setActiveIndex(-1)
  }

  const choose = (choice) => {
    onChange(choice.value)
    setFiltering(false)
    setOpen(false)
    setActiveIndex(-1)
    inputRef.current?.focus()
  }

  const handleKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) openAllOptions()
      setActiveIndex(index => Math.min(index + 1, choices.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex(index => Math.max(index - 1, 0))
    } else if (event.key === 'Enter' && open && activeIndex >= 0 && choices[activeIndex]) {
      event.preventDefault()
      choose(choices[activeIndex])
    } else if (event.key === 'Escape') {
      setOpen(false)
      setActiveIndex(-1)
    }
  }

  return (
    <div ref={wrapperRef} className={`relative ${className}`}>
      <input
        ref={inputRef}
        id={inputId}
        type="text"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open && choices.length > 0}
        aria-controls={listboxId}
        aria-activedescendant={activeIndex >= 0 ? `${listboxId}-${activeIndex}` : undefined}
        autoComplete="off"
        value={value}
        onChange={(event) => {
          onChange(event.target.value)
          setFiltering(true)
          setOpen(true)
          setActiveIndex(-1)
        }}
        onFocus={openAllOptions}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        required={required}
        disabled={disabled}
        className="w-full px-3 py-2 pr-9 border border-brand-line rounded text-sm bg-white text-brand-ink placeholder:text-brand-muted focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent disabled:opacity-50"
      />
      <button
        type="button"
        aria-label={`Show ${inputId.replaceAll('-', ' ')} options`}
        tabIndex={-1}
        onClick={() => {
          inputRef.current?.focus()
          openAllOptions()
        }}
        className="absolute right-0 top-0 h-full w-9 flex items-center justify-center text-brand-muted hover:text-brand-ink"
      >
        <ChevronDown size={15} aria-hidden="true" />
      </button>

      {open && choices.length > 0 && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute z-[60] top-full mt-1 w-full max-h-56 overflow-y-auto rounded-lg border border-brand-line bg-white py-1 shadow-xl"
        >
          {choices.map((choice, index) => (
            <button
              key={`${choice.type}-${choice.value}`}
              id={`${listboxId}-${index}`}
              type="button"
              role="option"
              aria-selected={activeIndex === index}
              onMouseDown={event => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(choice)}
              className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 ${
                activeIndex === index ? 'bg-brand-bg-soft text-brand-ink' : 'text-brand-ink hover:bg-brand-bg-soft'
              }`}
            >
              {choice.type === 'new' && <Plus size={14} className="shrink-0 text-brand-accent" aria-hidden="true" />}
              <span className="truncate">
                {choice.type === 'new' ? `Use new value: “${choice.value}”` : choice.value}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
