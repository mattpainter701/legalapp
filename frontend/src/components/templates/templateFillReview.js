// A populated value is separate from a reviewed value. Confidence describes
// the suggestion that actually supplied this value, never a manual override.
export const fillValue = value => value == null ? '' : String(value)

export function suggestionConfidenceLabel(review) {
  if (review.source?.source_type === 'firm_profile') return 'Saved firm profile value'
  return review.confidence == null ? 'Confidence unavailable' : `${review.confidence}% match confidence`
}

export function initialFillValues(names, fields) {
  return Object.fromEntries(names.map(name => [name, fields[name]?.field_type === 'checkbox' ? 'false' : '']))
}

export function discoverySuggestions(response) {
  const raw = response?.variables ?? response?.values ?? response?.field_values ?? {}
  const entries = Array.isArray(raw)
    ? raw.map(item => [item?.variable || item?.name || item?.key, item])
    : Object.entries(raw)
  return Object.fromEntries(entries.filter(([name]) => name).map(([name, item]) => [name,
    item && typeof item === 'object'
      ? { ...item, suggested_value: item.suggested_value ?? item.value ?? item.text ?? null }
      : { suggested_value: item },
  ]))
}

export function fillReview(names, fields, values, sources, reviewed) {
  const rows = names.filter(name => fields[name]?.field_type !== 'signature' && !fields[name]?.value_from).map(name => {
    const field = fields[name] || {}
    const value = fillValue(values[name])
    const present = field.field_type === 'checkbox'
      ? (field.required ? value === 'true' : ['true', 'false'].includes(value))
      : Boolean(value.trim())
    const source = sources[name]
    const fromSource = source?.suggested_value != null && fillValue(source.suggested_value) === value
    const rawConfidence = fromSource ? source.confidence : null
    const confidence = typeof rawConfidence === 'number' && Number.isFinite(rawConfidence) && rawConfidence >= 0 && rawConfidence <= 1 ? Math.round(rawConfidence * 100) : null
    const needsReview = present && fromSource && reviewed[name] !== value
    return { name, present, confidence, needsReview, source: fromSource ? source : null }
  })
  const completed = rows.filter(row => row.present).length
  return { rows, completed, total: rows.length, percent: rows.length ? Math.round(completed / rows.length * 100) : 100,
    remaining: rows.filter(row => !row.present), review: rows.filter(row => row.needsReview) }
}

export function applyFillSuggestions(names, fields, values, sources, suggestions) {
  const nextValues = { ...values }
  const nextSources = { ...sources }
  for (const name of names) {
    const suggestion = suggestions[name]
    if (suggestion?.suggested_value == null) continue
    // Unchecked checkboxes are real choices, not empty strings to overwrite.
    if (fillValue(values[name]).trim()) continue
    const value = fillValue(suggestion.suggested_value)
    if (fields[name]?.field_type === 'checkbox' && !['true', 'false'].includes(value)) continue
    nextValues[name] = value
    nextSources[name] = suggestion
  }
  return { values: nextValues, sources: nextSources }
}
