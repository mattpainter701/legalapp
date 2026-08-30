import { useEffect, useState } from 'react'
import { ArrowLeft, Download, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'

import { API_BASE_URL, getPublicOperatingContract, getPublicServiceStatus } from '../api'
import LawHandLogo from '../components/LawHandLogo'

const statusStyle = {
  implemented: 'border-emerald-700/25 bg-emerald-50 text-emerald-900',
  verified: 'border-blue-700/25 bg-blue-50 text-blue-900',
  'policy-committed': 'border-amber-700/25 bg-amber-50 text-amber-900',
  planned: 'border-slate-500/25 bg-slate-100 text-slate-800',
  unavailable: 'border-rose-700/25 bg-rose-50 text-rose-900',
}

function LoadingState() {
  return <div role='status' className='rounded-2xl border border-brand-line bg-brand-surface p-8 text-brand-ink-2'>Loading the current operating contract…</div>
}

export default function TrustCenterPage() {
  const [contract, setContract] = useState(null)
  const [serviceStatus, setServiceStatus] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([getPublicOperatingContract(), getPublicServiceStatus()])
      .then(([nextContract, nextStatus]) => {
        if (!active) return
        setContract(nextContract)
        setServiceStatus(nextStatus)
      })
      .catch(() => {
        if (active) setError('The live trust record is temporarily unavailable. Please try again shortly.')
      })
    return () => { active = false }
  }, [])

  return (
    <div className='min-h-screen bg-brand-bg text-brand-ink'>
      <a href='#trust-content' className='sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-brand-ink focus:px-4 focus:py-2 focus:text-white'>Skip to trust content</a>
      <header className='border-b border-brand-line bg-brand-surface'>
        <div className='mx-auto flex h-16 max-w-6xl items-center justify-between px-5 sm:px-6'>
          <Link to='/' aria-label='LawHand home'><LawHandLogo compact /></Link>
          <Link to='/' className='inline-flex min-h-11 items-center gap-2 rounded-xl border border-brand-line px-4 text-sm font-semibold hover:bg-brand-bg-soft'>
            <ArrowLeft size={16} aria-hidden='true' /> Home
          </Link>
        </div>
      </header>

      <main id='trust-content' tabIndex='-1'>
        <section className='border-b border-brand-line bg-brand-bg-soft/60'>
          <div className='mx-auto max-w-6xl px-5 py-14 sm:px-6 md:py-20'>
            <span className='inline-flex items-center gap-2 rounded-full border border-brand-line bg-brand-surface px-3 py-1.5 text-xs font-bold uppercase tracking-[0.14em] text-brand-accent-2'>
              <ShieldCheck size={14} aria-hidden='true' /> Evidence before claims
            </span>
            <h1 className='mt-5 max-w-4xl font-serif text-4xl font-bold tracking-tight sm:text-5xl'>LawHand trust center</h1>
            <p className='mt-5 max-w-3xl text-base leading-8 text-brand-ink-2 sm:text-lg'>Review the current operating scope, evidence boundary, provider dependencies, planned assurance work, and public incident state. Planned work is never presented as attained.</p>
            <div className='mt-7 flex flex-wrap gap-3'>
              <a href={`${API_BASE_URL}/public/security-review-packet`} className='inline-flex min-h-11 items-center gap-2 rounded-xl bg-brand-ink px-4 py-2 text-sm font-semibold text-white hover:bg-brand-ink-2'>
                <Download size={16} aria-hidden='true' /> Download security-review packet
              </a>
              <Link to='/privacy' className='inline-flex min-h-11 items-center rounded-xl border border-brand-line bg-brand-surface px-4 py-2 text-sm font-semibold hover:bg-brand-bg-soft'>Privacy policy</Link>
            </div>
          </div>
        </section>

        <div className='mx-auto max-w-6xl px-5 py-10 sm:px-6 md:py-14'>
          {error && <div role='alert' className='rounded-2xl border border-rose-300 bg-rose-50 p-5 text-rose-900'>{error}</div>}
          {!error && !contract && <LoadingState />}
          {contract && (
            <>
              <section aria-labelledby='service-state-title' className='rounded-2xl border border-brand-line bg-brand-surface p-6 shadow-sm'>
                <div className='flex flex-wrap items-start justify-between gap-4'>
                  <div>
                    <p className='text-xs font-bold uppercase tracking-[0.14em] text-brand-muted'>Contract version {contract.version}</p>
                    <h2 id='service-state-title' className='mt-2 font-serif text-2xl font-bold'>Published service state</h2>
                  </div>
                  <span className={`rounded-full border px-3 py-1.5 text-xs font-bold uppercase tracking-wide ${serviceStatus?.published_incident_state === 'none_active' ? statusStyle.implemented : statusStyle['policy-committed']}`}>
                    {serviceStatus?.published_incident_state === 'none_active' ? 'No active published incident' : 'Active incident'}
                  </span>
                </div>
                <p className='mt-4 max-w-4xl text-sm leading-6 text-brand-ink-2'>{contract.truth_rule}</p>
                <p className='mt-2 max-w-4xl text-sm leading-6 text-brand-muted'>The incident ledger reports published incident state; it does not independently assert current service health.</p>
              </section>

              <section aria-labelledby='controls-title' className='mt-10'>
                <h2 id='controls-title' className='font-serif text-3xl font-bold'>Operating controls and boundaries</h2>
                <div className='mt-6 grid gap-5 md:grid-cols-2'>
                  {contract.controls.map((control) => (
                    <article key={control.id} className='rounded-2xl border border-brand-line bg-brand-surface p-6 shadow-sm'>
                      <div className='flex items-start justify-between gap-4'>
                        <h3 className='font-serif text-xl font-bold'>{control.title}</h3>
                        <span className={`shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${statusStyle[control.status] || statusStyle.planned}`}>{control.status}</span>
                      </div>
                      <p className='mt-4 text-sm leading-6 text-brand-ink-2'>{control.claim}</p>
                      <div className='mt-4 rounded-xl bg-brand-bg-soft p-4'>
                        <p className='text-xs font-bold uppercase tracking-wide text-brand-muted'>Boundary</p>
                        <p className='mt-2 text-sm leading-6 text-brand-ink-2'>{control.boundary}</p>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
