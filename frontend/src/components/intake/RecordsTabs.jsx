import React, { useState } from 'react'

// tabs: [{ key, label, node }]
export default function RecordsTabs({ tabs }) {
  const [active, setActive] = useState(tabs[0]?.key)
  const current = tabs.find((t) => t.key === active) || tabs[0]
  return (
    <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setActive(t.key)}
            className={`rounded-xl px-3 py-1.5 text-xs font-bold ${
              active === t.key ? 'bg-brand-ink text-white' : 'bg-brand-bg-soft text-brand-muted'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {current?.node}
    </section>
  )
}
