import { useEffect, useState } from 'react'
import { getPlatformTenantCompliance } from '../api'

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

export default function PlatformComplianceCard({ platformKey, tenantId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setData(null)
    setError('')
    if (platformKey && tenantId) {
      getPlatformTenantCompliance(platformKey, tenantId)
        .then((next) => { if (active) setData(next) })
        .catch(() => { if (active) setError('Unable to load compliance posture') })
    }
    return () => { active = false }
  }, [platformKey, tenantId])

  if (error) return <div role="alert" className="text-sm text-brand-rose">{error}</div>
  if (!data) return <div aria-busy="true" className="text-sm text-brand-muted">Loading compliance posture…</div>

  const outstanding = data.agreements.agreements.filter((agreement) => agreement.required && !agreement.accepted)
  const matterFiles = data.retention.categories.find((category) => category.name === 'matter_files')
  const chatAttachments = data.retention.categories.find((category) => category.name === 'chat_attachments')
  return (
    <section aria-label="Tenant compliance posture">
      <h4 className="mb-3 text-xs font-bold uppercase tracking-wider text-brand-ink">Compliance posture</h4>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-brand-line bg-brand-surface p-3">
          <p className="text-xs text-brand-muted">Required agreements</p>
          <p className={`mt-1 text-sm font-semibold ${data.agreements.complete ? 'text-green-700' : 'text-brand-amber'}`}>
            {data.agreements.complete ? 'Current' : `${outstanding.length} outstanding`}
          </p>
          <p className="mt-1 text-xs text-brand-muted">Gate {data.agreements.enforced ? 'enforced' : 'rollout mode'}</p>
        </div>
        <div className="rounded-lg border border-brand-line bg-brand-surface p-3">
          <p className="text-xs text-brand-muted">Legal hold</p>
          <p className={`mt-1 text-sm font-semibold ${data.retention.legal_hold ? 'text-brand-rose' : 'text-green-700'}`}>{data.retention.legal_hold ? 'Active' : 'Not active'}</p>
          <p className="mt-1 truncate text-xs text-brand-muted">{data.retention.legal_hold_reason || 'No hold reason'}</p>
        </div>
        <div className="rounded-lg border border-brand-line bg-brand-surface p-3">
          <p className="text-xs text-brand-muted">Matter-file index</p>
          <p className="mt-1 text-sm font-semibold text-brand-ink">{Number(matterFiles?.record_count || 0).toLocaleString()} records</p>
          <p className="mt-1 text-xs text-brand-muted">{formatBytes(matterFiles?.bytes)} · customer/cloud control plane</p>
        </div>
        <div className="rounded-lg border border-brand-line bg-brand-surface p-3">
          <p className="text-xs text-brand-muted">Transient chat attachments</p>
          <p className="mt-1 text-sm font-semibold text-brand-ink">{Number(chatAttachments?.record_count || 0).toLocaleString()} records</p>
          <p className="mt-1 text-xs text-brand-muted">{formatBytes(chatAttachments?.bytes)} · policy v{data.retention.policy_version}</p>
        </div>
      </div>
    </section>
  )
}
