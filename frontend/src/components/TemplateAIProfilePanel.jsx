import { useEffect, useState } from 'react'
import { getTemplateAIProfile, saveTemplateAIProfile } from '../api'

export default function TemplateAIProfilePanel({ platformKey, providerKeys, onAuthError }) {
  const [profile, setProfile] = useState(null)
  const [activation, setActivation] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    let current = true
    setProfile(null)
    getTemplateAIProfile(platformKey).then((data) => {
      if (current) { setProfile(data.settings); setActivation(data.activation) }
    }).catch((e) => {
      if (current) {
        setError('Could not load the document template AI profile.')
        if (e?.response?.status === 403) onAuthError?.()
      }
    })
    return () => { current = false }
  }, [platformKey, onAuthError])
  const update = (name, value) => {
    setProfile((previous) => ({ ...previous, [name]: value }))
    setSaved(false)
  }
  const save = async () => {
    setBusy(true); setError(''); setSaved(false)
    try {
      const data = await saveTemplateAIProfile(platformKey, profile)
      setProfile(data.settings); setActivation(data.activation); setSaved(true)
    } catch (e) {
      const detail = e?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : detail?.message || 'Could not save the template AI profile. Check the key, rates, and gateway.')
      if (e?.response?.status === 403) onAuthError?.()
    } finally { setBusy(false) }
  }
  return (
    <section className="rounded-xl border border-brand-line bg-white p-5 space-y-4" aria-label="Document template premium AI">
      <div>
        <h3 className="font-semibold text-brand-ink">Document template premium AI</h3>
        <p className="text-sm text-brand-muted">Opus 5 via OpenRouter, used only when someone requests premium field suggestions in Template Studio. Chat and Background Automations keep their own profiles.</p>
        <p className="text-sm mt-2">Active configuration: {activation?.status?.replaceAll('_', ' ') || 'loading'}</p>
      </div>
      {error && <p role="alert" className="text-brand-rose">{error}</p>}
      {profile && <fieldset disabled={busy} className="space-y-4">
        <label className="flex items-center gap-2"><input type="checkbox" checked={profile.enabled} onChange={(e) => update('enabled', e.target.checked)} /> Enable premium template suggestions</label>
        <label className="block">Stored OpenRouter key
          <select className="block w-full mt-1 rounded border border-brand-line p-2" value={profile.key_id || ''} onChange={(e) => update('key_id', e.target.value || null)}>
            <option value="">Select an OpenRouter key</option>
            {providerKeys.filter((key) => key.provider_id === 'openrouter').map((key) => <option key={key.id} value={key.id}>{key.name}</option>)}
          </select>
        </label>
        <p className="text-sm text-brand-muted">Model: {profile.model}. Add or manage keys in the provider vault below.</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label>Input USD per million tokens<input className="block w-full mt-1 rounded border border-brand-line p-2" type="number" min="0.000001" max="1000" step="any" value={profile.input_usd_per_million} onChange={(e) => update('input_usd_per_million', e.target.value)} /></label>
          <label>Output USD per million tokens<input className="block w-full mt-1 rounded border border-brand-line p-2" type="number" min="0.000001" max="1000" step="any" value={profile.output_usd_per_million} onChange={(e) => update('output_usd_per_million', e.target.value)} /></label>
        </div>
        <p className="text-sm text-brand-muted">Confirm these rates against your provider agreement. Template usage is recorded separately; existing PAYG markup applies. Saving an enabled profile runs a small billable gateway test before activation.</p>
        <button className="btn-primary" disabled={busy || (profile.enabled && !profile.key_id)} onClick={save}>{busy ? 'Validating…' : 'Save template profile'}</button>
        {saved && <p role="status">Template profile saved{activation?.status === 'active' ? ' and validated' : ''}.</p>}
      </fieldset>}
    </section>
  )
}
