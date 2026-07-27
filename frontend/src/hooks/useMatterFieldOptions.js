import { useEffect, useState } from 'react'
import { getMatterFieldOptions } from '../api'

const DEFAULT_MATTER_TYPES = [
  'General', 'Litigation', 'Divorce', 'Child Custody', 'Child Support',
  'Estate Planning', 'Probate', 'Contract', 'Employment', 'Real Estate',
  'Corporate', 'Criminal Defense', 'Immigration',
]

const DEFAULT_ROLES = [
  'Client', 'Plaintiff', 'Defendant', 'Petitioner', 'Respondent',
  'Appellant', 'Appellee', 'Creditor', 'Debtor',
]

const DEFAULT_JURISDICTIONS = [
  'Federal',
  'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut',
  'Delaware', 'District of Columbia', 'Florida', 'Georgia', 'Hawaii', 'Idaho',
  'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine',
  'Maryland', 'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi', 'Missouri',
  'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey', 'New Mexico',
  'New York', 'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma', 'Oregon',
  'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee',
  'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington', 'West Virginia',
  'Wisconsin', 'Wyoming',
]

const mergeOptions = (...groups) => {
  const values = []
  const seen = new Set()
  groups.flat().forEach((rawValue) => {
    const value = String(rawValue || '').trim()
    const key = value.toLocaleLowerCase()
    if (!value || seen.has(key)) return
    seen.add(key)
    values.push(value)
  })
  return values
}

const defaults = {
  matter_types: DEFAULT_MATTER_TYPES,
  roles: DEFAULT_ROLES,
  jurisdictions: DEFAULT_JURISDICTIONS,
  counterparties: [],
}

export default function useMatterFieldOptions(enabled = true) {
  const [options, setOptions] = useState(defaults)

  useEffect(() => {
    if (!enabled) return undefined
    let cancelled = false
    getMatterFieldOptions()
      .then((data) => {
        if (cancelled) return
        setOptions({
          matter_types: mergeOptions(data?.matter_types, DEFAULT_MATTER_TYPES),
          roles: mergeOptions(data?.roles, DEFAULT_ROLES),
          jurisdictions: mergeOptions(data?.jurisdictions, DEFAULT_JURISDICTIONS),
          counterparties: mergeOptions(data?.counterparties),
        })
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [enabled])

  return options
}
