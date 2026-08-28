import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { Activity, AlertTriangle, CheckCircle2, RefreshCw, Server, ShieldAlert } from 'lucide-react'
import { createPlatformSession } from '../api'

const BASE_URL = import.meta.env.VITE_API_URL || '/api'

const tone = {
  healthy: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  degraded: 'border-amber-200 bg-amber-50 text-amber-900',
  unavailable: 'border-red-200 bg-red-50 text-red-800',
  unconfigured: 'border-slate-200 bg-slate-50 text-slate-700',
}

function Login({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const session = await createPlatformSession(key)
      onLogin(session.access_token)
      setKey('')
    } catch {
      setError('Platform authentication failed.')
    } finally {
      setLoading(false)
    }
  }

  return <main className="min-h-screen bg-brand-bg px-6 py-20">
    <form onSubmit={submit} className="mx-auto max-w-md rounded-2xl border border-brand-line bg-white p-7 shadow-sm">
      <ShieldAlert className="mb-4 text-brand-accent" aria-hidden="true" />
      <h1 className="font-display text-2xl font-bold text-brand-ink">Infrastructure status</h1>
      <p className="mt-2 text-sm text-brand-muted">Operator access is kept only in memory for this page.</p>
      <label className="mt-6 block text-sm font-semibold text-brand-ink" htmlFor="platform-infra-key">Platform bootstrap secret</label>
      <input id="platform-infra-key" type="password" autoComplete="current-password" value={key} onChange={(event) => setKey(event.target.value)} className="mt-2 w-full rounded-lg border border-brand-line px-3 py-2.5" required />
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <button disabled={loading} className="mt-5 w-full rounded-lg bg-brand-accent px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60">{loading ? 'Authenticating…' : 'Open status'}</button>
    </form>
  </main>
}

export default function PlatformInfrastructurePage() {
  const [token, setToken] = useState('')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!token) return
    setLoading(true)
    setError('')
    try {
      const response = await axios.get(`${BASE_URL}/platform/infrastructure`, { headers: { Authorization: `Bearer ${token}` } })
      setData(response.data)
    } catch (requestError) {
      if (requestError.response?.status === 401) setToken('')
      else setError('Infrastructure status could not be refreshed.')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { refresh() }, [refresh])
  if (!token) return <Login onLogin={setToken} />

  return <main className="min-h-screen bg-brand-bg px-4 py-10 sm:px-8">
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div><p className="text-xs font-bold uppercase tracking-widest text-brand-accent">Platform operations</p><h1 className="mt-1 font-display text-3xl font-bold text-brand-ink">Sites, DR, and alerts</h1></div>
        <button type="button" onClick={refresh} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-brand-line bg-white px-4 py-2 text-sm font-semibold text-brand-ink disabled:opacity-60"><RefreshCw size={15} className={loading ? 'animate-spin' : ''} />Refresh</button>
      </div>
      {error && <p role="alert" className="mt-5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {data && <>
        <section className={`mt-6 flex items-center gap-3 rounded-xl border p-4 ${tone[data.status]}`}>
          {data.status === 'healthy' ? <CheckCircle2 /> : <AlertTriangle />}
          <div><p className="font-bold capitalize">Overall {data.status}</p><p className="text-xs">Checked {new Date(data.checked_at).toLocaleString()}</p></div>
        </section>
        <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4" aria-label="Infrastructure services">
          {data.services.map((service) => <article key={service.id} className="rounded-xl border border-brand-line bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between"><Server size={18} className="text-brand-accent" /><span className={`rounded-full border px-2 py-0.5 text-xs font-bold capitalize ${tone[service.status]}`}>{service.status}</span></div>
            <h2 className="mt-4 font-bold text-brand-ink">{service.label}</h2><p className="text-xs uppercase tracking-wide text-brand-muted">{service.role}</p>
            <p className="mt-3 text-sm text-brand-ink-2">{service.detail}</p>
            {service.release_sha && <p className="mt-3 font-mono text-xs text-brand-muted">{service.release_sha.slice(0, 12)}</p>}
            {service.writer_enabled != null && <p className="mt-2 text-xs font-semibold text-brand-muted">Writer: {service.writer_enabled ? 'enabled' : 'fenced'}</p>}
          </article>)}
          {data.services.length === 0 && <div className="col-span-full rounded-xl border border-dashed border-brand-line bg-white p-8 text-center text-sm text-brand-muted">Status targets have not been configured in production yet.</div>}
        </section>
        <section className="mt-6 rounded-xl border border-brand-line bg-white p-5 shadow-sm">
          <div className="flex items-center gap-2"><Activity size={18} className="text-brand-accent" /><h2 className="font-bold text-brand-ink">Active infrastructure alerts</h2></div>
          {data.alerts.length === 0 ? <p className="mt-4 text-sm text-emerald-700">No active infrastructure alerts.</p> : <ul className="mt-4 space-y-2">{data.alerts.map((alert) => <li key={`${alert.service_id}-${alert.summary}`} className={`rounded-lg border p-3 text-sm ${alert.severity === 'critical' ? tone.unavailable : tone.degraded}`}><strong className="mr-2 uppercase text-xs">{alert.severity}</strong>{alert.summary}</li>)}</ul>}
        </section>
      </>}
    </div>
  </main>
}
