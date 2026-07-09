import React from 'react'
import { Link } from 'react-router-dom'

export default function LegalNoticePage({ type }) {
  const privacy = type === 'privacy'
  return (
    <main className="min-h-screen bg-brand-bg px-4 py-16">
      <article className="mx-auto max-w-3xl rounded-2xl border border-brand-line bg-brand-surface p-8 shadow-sm">
        <Link to="/" className="text-sm font-medium text-brand-accent hover:text-brand-accent-2">Clarity Legal</Link>
        <h1 className="mt-5 font-serif text-3xl text-brand-ink">{privacy ? 'Privacy summary' : 'Service summary'}</h1>
        <p className="mt-5 text-sm leading-7 text-brand-ink-2">
          {privacy
            ? 'Clarity Legal processes account and workspace data to provide the service. Firm data is isolated by tenant. Model-provider data handling depends on the provider and tenant configuration selected by your organization. Your firm administrator controls connected services and available retention settings.'
            : 'This page is a product summary, not a contract. Use of Clarity Legal is governed by the subscription agreement provided to your organization. The service assists legal professionals but does not replace professional judgment, source verification, or your firm’s compliance obligations.'}
        </p>
        <p className="mt-4 text-sm leading-7 text-brand-ink-2">
          The controlling subscription terms and, where applicable, data-processing agreement are provided by your organization. Contact your firm administrator for those documents, the applicable retention policy, subprocessors, and workspace-specific privacy terms.
        </p>
      </article>
    </main>
  )
}
