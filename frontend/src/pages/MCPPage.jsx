import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Copy, KeyRound, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import {
  createMcpProductKey,
  getAdminMcpOverview,
  getMcpProductKeys,
  revokeMcpProductKey,
  updateAdminSettings,
} from '../api'
import { useAuth } from '../App'
import { AlertBanner, Toggle } from '../components/ui'

function CopyButton({ value }) {
  const [copyState, setCopyState] = useState('idle')
  const handle = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(value)
      setCopyState('copied')
      setTimeout(() => setCopyState('idle'), 1600)
    } catch {
      setCopyState('failed')
      setTimeout(() => setCopyState('idle'), 2200)
    }
  }
  const label = copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'
  return (
    <button
      type="button"
      onClick={handle}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-brand-line text-brand-muted hover:text-brand-ink"
      title={label}
      aria-label={`${label} ${value}`}
    >
      <Copy size={15} />
    </button>
  )
}

function CodeBlock({ value, label }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase text-brand-muted">{label}</p>
      <div className="flex items-start gap-2 rounded-lg border border-brand-line bg-brand-bg px-3 py-2">
        <code className="min-w-0 flex-1 whitespace-pre-wrap break-words font-mono text-xs leading-5 text-brand-ink-2">{value}</code>
        <CopyButton value={value} />
      </div>
    </div>
  )
}

const WORKSPACE_URL = 'https://mcp.getlawhand.com/api/mcp/workspace'
const WORKSPACE_SHORTHAND = 'https://mcp.getlawhand.com'
const codexSetup = (url) => `codex mcp add lawhandWorkspace --url ${url}
codex mcp login lawhandWorkspace
codex mcp list`
const claudeSetup = (url) => `claude mcp add --transport http --scope user lawhand ${url}`

function MetricCard({ label, value, detail }) {
  return (
    <div className="rounded-xl border border-brand-line bg-brand-surface p-4 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-muted">{label}</p>
      <p className="mt-2 text-lg font-semibold text-brand-ink">{value}</p>
      {detail && <p className="mt-1 text-xs text-brand-muted">{detail}</p>}
    </div>
  )
}

function PolicyControl({ checked, description, disabled, label, onChange }) {
  return (
    <div className="flex items-center justify-between gap-5 rounded-lg border border-brand-line bg-brand-bg px-4 py-3">
      <div>
        <p className="text-sm font-semibold text-brand-ink">{label}</p>
        <p className="mt-1 text-xs leading-5 text-brand-muted">{description}</p>
      </div>
      <Toggle checked={checked} disabled={disabled} label={label} onChange={onChange} />
    </div>
  )
}

function ToolList({ emptyLabel, tools, variant = 'read' }) {
  if (!tools.length) {
    return <p className="rounded-lg bg-brand-bg px-3 py-4 text-xs text-brand-muted">{emptyLabel}</p>
  }
  const border = variant === 'propose' ? 'border-amber-400' : 'border-brand-accent'
  return tools.map((tool) => (
    <div key={tool.name} className={`mb-3 border-l-2 pl-3 ${border}`}>
      <p className="font-mono text-xs font-semibold text-brand-ink">{tool.name}</p>
      <p className="mt-1 text-xs leading-5 text-brand-muted">{tool.description}</p>
    </div>
  ))
}

function WorkspaceSection({ embedded, overview, onNavigateUsers, onToggle, savingSetting }) {
  const workspace = overview.workspace
  const users = workspace.users || {}
  const tools = workspace.tools || []
  const reads = tools.filter((tool) => tool.effect === 'read')
  const proposals = tools.filter((tool) => tool.effect === 'propose')
  const deploymentEnabled = workspace.deployment_enabled === true
  const tenantEnabled = workspace.tenant_enabled === true
  const effectiveEnabled = deploymentEnabled && tenantEnabled
  const deploymentLabel = deploymentEnabled ? 'Available' : 'Unavailable'
  const officialUrl = workspace.official_url || WORKSPACE_URL
  const shorthand = workspace.shorthand || WORKSPACE_SHORTHAND
  const tenantLabel = effectiveEnabled
    ? 'Enabled'
    : deploymentEnabled
      ? 'Disabled by tenant'
      : 'Platform unavailable'
  const controlsDisabled = Boolean(savingSetting)
  const SectionHeading = embedded ? 'h3' : 'h2'

  return (
    <section className="space-y-5" aria-labelledby="workspace-mcp-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-brand-accent">Primary tenant service · Workspace access</p>
          <SectionHeading id="workspace-mcp-heading" className="mt-1 font-serif text-2xl font-bold text-brand-ink">LawHand Platform MCP</SectionHeading>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-brand-muted">
            Connect approved Claude, ChatGPT, Codex, and compatible assistants to bounded firm workflows. Access is tenant-controlled, scoped per user, and audited.
          </p>
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${effectiveEnabled ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900'}`}>
          {tenantLabel}
        </span>
      </div>
      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard label="Deployment" value={deploymentLabel} detail="Controlled by LawHand operations" />
        <MetricCard label="Tenant access" value={tenantLabel} detail="Administrator master policy" />
        <MetricCard label="Enabled users" value={users.enabled ?? 0} detail={`${users.configured_enabled ?? users.enabled ?? 0} user policies on · ${users.licensed ?? 0} licensed · ${users.privacy_mode_blocked ?? 0} blocked by Privacy Mode`} />
        <MetricCard label="Active grants" value={workspace.active_grants ?? 0} detail="User-approved OAuth connections" />
      </div>
      <div className="rounded-xl border border-brand-line bg-brand-surface p-5">
        <div>
          <p className="font-semibold text-brand-ink">Tenant controls</p>
          <p className="mt-1 text-xs leading-5 text-brand-muted">These policies are separate from each user's license, individual permission, Privacy Mode, and OAuth consent.</p>
        </div>
        {!deploymentEnabled && (
          <AlertBanner type="warning" title="Platform MCP is unavailable" className="mt-4">
            LawHand operations must enable the deployment before tenant connections can be authorized.
          </AlertBanner>
        )}
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          <PolicyControl
            checked={tenantEnabled}
            description="Disabling this revokes every active Workspace MCP grant. Re-enabling requires each user to reconnect and consent again."
            disabled={controlsDisabled || (!deploymentEnabled && !tenantEnabled)}
            label="Enable Platform MCP for this tenant"
            onChange={(value) => onToggle('workspace_mcp_enabled', value)}
          />
          <PolicyControl
            checked={workspace.default_user_enabled === true}
            description="Applies only to users created or directory-synced later. Existing users keep their individual policy."
            disabled={controlsDisabled}
            label="Enable Platform MCP for new users"
            onChange={(value) => onToggle('default_workspace_mcp_enabled', value)}
          />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-brand-line pt-4">
          <button type="button" onClick={onNavigateUsers} className="text-sm font-medium text-brand-accent hover:underline">Manage existing users</button>
          <span className="text-xs text-brand-muted">Privacy Mode is user-controlled and always blocks external workspace access.</span>
        </div>
        <details className="mt-3 rounded-lg border border-brand-line bg-brand-surface px-3 py-2">
          <summary className="cursor-pointer text-xs font-semibold text-brand-ink">Why does Privacy Mode block MCP, and how is it turned off?</summary>
          <p className="mt-2 text-xs leading-5 text-brand-muted">Privacy Mode is the user's Profile → Protect private details switch. When it is on, LawHand pauses external access to that user's workspace and revokes their Platform MCP grants so workspace content cannot leave through MCP. The user must turn that switch off in Profile; a tenant administrator cannot override it. The user then reconnects and consents again. Research MCP is separate and accesses public authority only.</p>
        </details>
      </div>
      <div className="rounded-xl border border-brand-line bg-brand-surface p-5">
        <p className="font-semibold text-brand-ink">Connection and setup</p>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <CodeBlock label="Official MCP URL" value={officialUrl} />
          <CodeBlock label="Supported shorthand" value={shorthand} />
        </div>
        <p className="mt-3 text-xs leading-5 text-brand-muted">OAuth 2.1 is required. Never put a Research key or static bearer token in a Platform MCP configuration.</p>
        <details className="mt-4 rounded-lg border border-brand-line p-3">
          <summary className="cursor-pointer text-sm font-semibold text-brand-ink">Codex / ChatGPT setup</summary>
          <div className="mt-3"><CodeBlock label="Codex CLI" value={codexSetup(officialUrl)} /><p className="mt-2 text-xs leading-5 text-brand-muted">ChatGPT workspace: an admin first permits custom MCP apps under Workspace Settings → Permissions &amp; Roles → Connected Data. Enable Developer mode under Settings → Apps → Advanced Settings, then use Apps → Create, paste the official URL, choose OAuth, and scan tools.</p></div>
        </details>
        <details className="mt-3 rounded-lg border border-brand-line p-3">
          <summary className="cursor-pointer text-sm font-semibold text-brand-ink">Claude Desktop / Claude Code setup</summary>
          <div className="mt-3"><CodeBlock label="Claude Code" value={claudeSetup(officialUrl)} /><p className="mt-2 text-xs leading-5 text-brand-muted">Then run <code>/mcp</code> and authenticate <code>lawhand</code>. In Claude Desktop, add the official URL under Settings → Connectors → Add custom connector.</p></div>
        </details>
      </div>
      <div className="rounded-xl border border-brand-line bg-brand-surface p-5">
        <div className="flex items-baseline justify-between gap-3"><p className="font-semibold text-brand-ink">Available tool calls</p><p className="text-xs text-brand-muted">{tools.length} published</p></div>
        <p className="mt-1 text-xs leading-5 text-brand-muted">Read tools return bounded workspace information. Proposal tools create reviewable work only. There are no MCP tools for approval, filing, sending, delivery, or execution.</p>
        <div className="mt-4 grid gap-5 md:grid-cols-2">
          <div><p className="mb-2 text-xs font-semibold uppercase text-brand-muted">Reads</p><ToolList emptyLabel="No read tools are currently published." tools={reads} /></div>
          <div><p className="mb-2 text-xs font-semibold uppercase text-brand-muted">Proposals · human review</p><ToolList emptyLabel="No proposal tools are currently published." tools={proposals} variant="propose" /></div>
        </div>
      </div>
    </section>
  )
}

const TOOL_DOCS = [
  ['search_caselaw', 'Hybrid vector and keyword search across CourtListener authority'],
  ['search_legal_authorities', 'Search locally reviewed statutes, regulations, court rules, forms, and official guidance'],
  ['get_case_details', 'Fetch opinion and docket metadata for a case'],
  ['get_full_opinion', 'Return complete locally loaded opinion text'],
  ['find_similar_cases', 'Find factually similar cases from a query or known opinion'],
  ['search_by_citation', 'Resolve a citation into local CourtListener authority'],
  ['validate_citation', 'Parse a citation and report whether it resolves locally'],
  ['normalize_citation', 'Return canonical citation fields for messy user input'],
  ['get_citation_network', 'Inspect bounded local citation relationships'],
  ['get_authority_treatment', 'Show local citation-history and treatment signals'],
  ['search_by_jurisdiction', 'Filter authority by court or jurisdiction'],
  ['search_recent_authority', 'Find newer authority in the local corpus'],
  ['get_court_info', 'Return court metadata'],
  ['get_court_coverage', 'Show loaded courts, date ranges, and local corpus limits'],
  ['search_dockets', 'Search locally loaded docket metadata'],
  ['export_research_bundle', 'Package selected cases and citations for drafting workflows'],
  ['sync_status', 'Show ingest and embedding progress'],
  ['corpus_status', 'Show global local corpus counts and coverage'],
]

export default function MCPPage({ embedded = false }) {
  const confirmAction = useConfirm()
  const { user } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [overview, setOverview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [researchRefreshing, setResearchRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [workspaceError, setWorkspaceError] = useState(null)
  const [researchError, setResearchError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [newKey, setNewKey] = useState(null)
  const [form, setForm] = useState({
    name: 'LawHand Research',
    monthly_call_limit: '5000',
    burst_limit_per_minute: '60',
    allowed_tools: [],
  })
  const [overviewSaving, setOverviewSaving] = useState(null)

  // This page is the Research MCP surface.  Keep workspace/platform tools out
  // of the selector even if an older gateway response includes them.
  const researchToolNames = useMemo(() => new Set(TOOL_DOCS.map(([name]) => name)), [])
  const tools = useMemo(() => {
    if (!Array.isArray(data?.tools)) return TOOL_DOCS.map(([name]) => name)
    return data.tools.filter((name) => researchToolNames.has(name))
  }, [data, researchToolNames])
  const catalogAvailable = tools.length > 0
  const allToolsSelected = form.allowed_tools.length === 0 || form.allowed_tools.length === tools.length

  const errorDetail = (error, fallback) => error?.response?.data?.detail || fallback

  const loadWorkspace = async () => {
    setWorkspaceError(null)
    try {
      setOverview(await getAdminMcpOverview())
    } catch (error) {
      setOverview(null)
      setWorkspaceError(errorDetail(error, 'Failed to load Platform MCP status'))
    }
  }

  const loadResearch = async ({ refresh = false } = {}) => {
    if (refresh) setResearchRefreshing(true)
    setResearchError(null)
    try {
      setData(await getMcpProductKeys())
    } catch (error) {
      if (!data) setData(null)
      setResearchError(errorDetail(error, 'Failed to load Research MCP access'))
    } finally {
      if (refresh) setResearchRefreshing(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([getAdminMcpOverview(), getMcpProductKeys()]).then(([workspaceResult, researchResult]) => {
      if (cancelled) return
      if (workspaceResult.status === 'fulfilled') {
        setOverview(workspaceResult.value)
      } else {
        setWorkspaceError(errorDetail(workspaceResult.reason, 'Failed to load Platform MCP status'))
      }
      if (researchResult.status === 'fulfilled') {
        setData(researchResult.value)
      } else {
        setResearchError(errorDetail(researchResult.reason, 'Failed to load Research MCP access'))
      }
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  const updateWorkspaceSetting = async (key, value) => {
    if (key === 'workspace_mcp_enabled' && value === false) {
      const confirmed = await confirmAction({
        title: 'Disable Platform MCP for this tenant?',
        message: 'Every active Workspace MCP grant will be revoked immediately. Connected clients will stop working, and users must reconnect and consent again after you re-enable access.',
        confirmLabel: 'Disable and revoke grants',
        destructive: true,
      })
      if (!confirmed) return
    }
    setActionError(null)
    setOverviewSaving(key)
    try {
      await updateAdminSettings({ [key]: value })
      try {
        setOverview(await getAdminMcpOverview())
        setWorkspaceError(null)
      } catch (error) {
        setOverview(null)
        setWorkspaceError(errorDetail(error, 'The setting was saved, but Platform MCP status could not be reloaded'))
      }
    } catch (error) {
      setActionError(errorDetail(error, 'Failed to save Platform MCP setting'))
    } finally {
      setOverviewSaving(null)
    }
  }

  const toggleTool = (tool) => {
    setForm((prev) => {
      const current = prev.allowed_tools.length === 0 ? tools : prev.allowed_tools
      const exists = current.includes(tool)
      const nextTools = exists
        ? current.filter((item) => item !== tool)
        : [...current, tool]
      return {
        ...prev,
        allowed_tools: nextTools.length === tools.length ? [] : nextTools,
      }
    })
  }

  const handleCreate = async (event) => {
    event.preventDefault()
    if (!catalogAvailable) return
    setSaving(true)
    setActionError(null)
    try {
      const result = await createMcpProductKey({
        name: form.name,
        monthly_call_limit: Number(form.monthly_call_limit),
        burst_limit_per_minute: Number(form.burst_limit_per_minute),
        allowed_tools: allToolsSelected ? null : form.allowed_tools,
      })
      setNewKey(result.api_key)
      setForm({ name: 'LawHand Research', monthly_call_limit: '5000', burst_limit_per_minute: '60', allowed_tools: [] })
      await getMcpProductKeys().then(setData)
    } catch (error) {
      setActionError(errorDetail(error, 'Failed to create MCP key'))
    } finally {
      setSaving(false)
    }
  }

  const handleRevoke = async (key) => {
    if (!await confirmAction({ title: `Revoke ${key.name}?`, message: 'Existing clients using this key will stop working.', confirmLabel: 'Revoke key', destructive: true })) return
    setActionError(null)
    try {
      await revokeMcpProductKey(key.id)
      await getMcpProductKeys().then(setData)
    } catch (error) {
      setActionError(errorDetail(error, 'Failed to revoke MCP key'))
    }
  }

  if (user?.role !== 'admin') {
    return (
      <div className="flex min-h-[320px] items-center justify-center bg-brand-bg">
        <p className="font-sans text-brand-muted">Admin access required.</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex min-h-[320px] items-center justify-center bg-brand-bg" role="status" aria-label="Loading MCP servers">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-brand-accent border-t-transparent" aria-hidden="true" />
      </div>
    )
  }

  const usage = data?.usage || { total_calls: 0, total_results: 0 }
  const PageHeading = embedded ? 'h2' : 'h1'
  const ResearchHeading = embedded ? 'h3' : 'h2'
  const researchEnabled = data?.product_enabled === true
  const researchStatus = data == null ? 'Status unavailable' : researchEnabled ? 'Enabled' : 'Release-gated'
  const researchEndpoint = data?.mcp_server_url || 'https://research.getlawhand.com/api/mcp'
  const researchShorthand = data?.shorthand || 'https://research.getlawhand.com'

  return (
    <div className={embedded ? '' : 'min-h-screen bg-brand-bg'}>
      <div className={embedded ? 'space-y-8' : 'mx-auto max-w-5xl space-y-8 px-4 py-10'}>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-accent">Tenant administration</p>
            <PageHeading className="mt-1 font-serif text-3xl font-bold text-brand-ink">MCP Servers</PageHeading>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-brand-muted">
              Control tenant access, inspect published tools, and copy supported client setup for LawHand MCP services.
            </p>
          </div>
          {!embedded && (
            <button type="button" onClick={() => navigate(-1)} className="text-sm text-brand-muted hover:text-brand-ink">Back</button>
          )}
        </header>

        {actionError && (
          <AlertBanner title="MCP action failed" onDismiss={() => setActionError(null)}>{actionError}</AlertBanner>
        )}

        {overview ? (
          <WorkspaceSection
            embedded={embedded}
            overview={overview}
            onNavigateUsers={() => navigate('/admin?tab=users')}
            onToggle={updateWorkspaceSetting}
            savingSetting={overviewSaving}
          />
        ) : (
          <AlertBanner title="Platform MCP status is unavailable" actionLabel="Retry" onAction={loadWorkspace}>
            {workspaceError || 'LawHand could not load the tenant MCP policy. No enabled state is being assumed.'}
          </AlertBanner>
        )}
        {overviewSaving && <p className="text-xs text-brand-muted" role="status">Saving Platform MCP settings…</p>}

        <section className="space-y-5 border-t border-brand-line pt-8" aria-labelledby="research-mcp-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Separate service · Research-only PAYG</p>
              <ResearchHeading id="research-mcp-heading" className="mt-1 font-serif text-2xl font-bold text-brand-ink">LawHand Research MCP</ResearchHeading>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-brand-muted">
                External access to approved legal authority without exposing tenant workspace data. Hosted clients use OAuth 2.1; header-capable clients can use scoped <code>lhrk_…</code> product keys.
              </p>
            </div>
            <span className={`rounded-full px-3 py-1 text-xs font-semibold ${researchEnabled ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900'}`}>{researchStatus}</span>
          </div>

          {researchError && (
            <AlertBanner title="Research MCP status is unavailable" actionLabel="Retry" onAction={() => loadResearch()}>{researchError}</AlertBanner>
          )}

          {data == null ? (
            <div className="space-y-4 rounded-xl border border-brand-line bg-brand-surface p-5">
              <p className="text-sm text-brand-muted">Key issuance and billing status are hidden until the administration API can be loaded.</p>
              <CodeBlock label="Official Research MCP URL" value="https://research.getlawhand.com/api/mcp" />
            </div>
          ) : !researchEnabled ? (
            <div className="space-y-4">
              <AlertBanner type="warning" title="External Research MCP access is not enabled">
                Research PAYG key issuance is unavailable until LawHand operations enables the Research product and its billing and authorization configuration.
              </AlertBanner>
              <div className="grid gap-3 md:grid-cols-2">
                <CodeBlock label="Official Research MCP URL (release-gated)" value="https://research.getlawhand.com/api/mcp" />
                <CodeBlock label="Supported shorthand" value={researchShorthand} />
              </div>
              <p className="text-xs leading-5 text-brand-muted">Research is separate from Platform MCP. No key can be generated while this gate is closed.</p>
            </div>
          ) : (
            <>
              {newKey && (
                <AlertBanner type="warning" title="New Research API key — copy it now">
                  <div className="mt-2 flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all rounded-md bg-white px-3 py-2 font-mono text-sm text-amber-900">{newKey}</code>
                    <CopyButton value={newKey} />
                  </div>
                </AlertBanner>
              )}

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard label="30-day calls" value={usage.total_calls || 0} />
                <MetricCard label="Returned results" value={usage.total_results || 0} />
                <MetricCard label="Active keys" value={(data.keys || []).filter((key) => key.is_active).length} />
                <MetricCard label="Entitlement" value={data.entitlement_status || 'Unknown'} />
                <MetricCard label="Billing" value={data.billing_status || 'Unknown'} />
              </div>

              <div className="rounded-xl border border-brand-line bg-brand-surface p-5">
                <p className="font-semibold text-brand-ink">Connection endpoints</p>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <CodeBlock label="Official MCP URL" value={researchEndpoint} />
                  <CodeBlock label="Supported shorthand" value={researchShorthand} />
                  <CodeBlock label="API client header" value={`${data.auth_header || 'X-MCP-API-Key'}: lhrk_...`} />
                </div>
                <p className="mt-3 text-xs leading-5 text-brand-muted">
                  Publish the full URL; the shorthand remains supported. OAuth is for hosted clients, while product keys are for clients that can set request headers. LiteLLM remains an internal model gateway and its credentials are never exposed as Research MCP keys.
                </p>
              </div>

              <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
                <form onSubmit={handleCreate} className="rounded-xl border border-brand-line bg-brand-surface p-5">
                  <div className="mb-4 flex items-center gap-2">
                    <KeyRound size={18} className="text-brand-accent" aria-hidden="true" />
                    <p className="text-sm font-semibold text-brand-ink">Create Research product key</p>
                  </div>
                  <label className="mb-3 block">
                    <span className="mb-1 block text-xs font-semibold uppercase text-brand-muted">Name</span>
                    <input value={form.name} onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))} className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink" required />
                  </label>
                  <label className="mb-4 block">
                    <span className="mb-1 block text-xs font-semibold uppercase text-brand-muted">Monthly call limit</span>
                    <input type="number" min="1" value={form.monthly_call_limit} onChange={(event) => setForm((prev) => ({ ...prev, monthly_call_limit: event.target.value }))} className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink" required />
                  </label>
                  <label className="mb-4 block">
                    <span className="mb-1 block text-xs font-semibold uppercase text-brand-muted">Burst limit per minute</span>
                    <input type="number" min="1" value={form.burst_limit_per_minute} onChange={(event) => setForm((prev) => ({ ...prev, burst_limit_per_minute: event.target.value }))} className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink" required />
                  </label>
                  <fieldset className="mb-5" disabled={!catalogAvailable || saving}>
                    <legend className="mb-2 text-xs font-semibold uppercase text-brand-muted">Allowed tools</legend>
                    {catalogAvailable ? (
                      <>
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <p className="text-xs text-brand-muted">New keys allow every published tool unless you remove one.</p>
                          <button type="button" onClick={() => setForm((prev) => ({ ...prev, allowed_tools: [] }))} className="shrink-0 rounded-md border border-brand-line px-2.5 py-1 text-xs font-medium text-brand-muted hover:text-brand-ink">Allow all</button>
                        </div>
                        <div className="space-y-2">
                          {tools.map((tool) => (
                            <label key={tool} className="flex items-center gap-2 text-sm text-brand-ink">
                              <input type="checkbox" checked={form.allowed_tools.length === 0 || form.allowed_tools.includes(tool)} onChange={() => toggleTool(tool)} className="h-4 w-4 rounded border-brand-line" />
                              <span className="font-mono text-xs">{tool}</span>
                            </label>
                          ))}
                        </div>
                      </>
                    ) : (
                      <AlertBanner type="warning" title="Research tool catalog is unavailable">Key creation is disabled so an empty catalog cannot silently grant unintended access.</AlertBanner>
                    )}
                  </fieldset>
                  <button type="submit" disabled={saving || !catalogAvailable} className="inline-flex items-center gap-2 rounded-md bg-brand-accent px-4 py-2 text-sm font-medium text-white hover:bg-brand-accent-2 disabled:cursor-not-allowed disabled:opacity-60">
                    <Plus size={16} aria-hidden="true" />
                    {saving ? 'Creating' : 'Create key'}
                  </button>
                </form>

                <div className="rounded-xl border border-brand-line bg-brand-surface p-5">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-brand-ink">Keys and usage</p>
                    <button type="button" onClick={() => loadResearch({ refresh: true })} disabled={researchRefreshing} className="inline-flex items-center gap-2 rounded-md border border-brand-line px-3 py-2 text-sm text-brand-muted hover:text-brand-ink disabled:opacity-60">
                      <RotateCcw size={15} aria-hidden="true" />
                      {researchRefreshing ? 'Refreshing' : 'Refresh'}
                    </button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead className="text-xs uppercase text-brand-muted">
                        <tr>
                          <th scope="col" className="pb-2 pr-3">Name</th>
                          <th scope="col" className="pb-2 pr-3">Key</th>
                          <th scope="col" className="pb-2 pr-3">Calls</th>
                          <th scope="col" className="pb-2 pr-3">Limit</th>
                          <th scope="col" className="pb-2 pr-3">Status</th>
                          <th scope="col" className="pb-2"><span className="sr-only">Actions</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        {(data.keys || []).map((key) => (
                          <tr key={key.id} className="border-t border-brand-line">
                            <td className="py-3 pr-3 font-medium text-brand-ink">{key.name}</td>
                            <td className="py-3 pr-3 font-mono text-xs text-brand-muted">{key.api_key_masked}</td>
                            <td className="py-3 pr-3 text-brand-ink">{key.usage?.calls || 0}</td>
                            <td className="py-3 pr-3 text-brand-muted">{key.monthly_call_limit ?? 'Not configured'}</td>
                            <td className="py-3 pr-3"><span className={key.is_active ? 'text-emerald-700' : 'text-brand-muted'}>{key.is_active ? 'Active' : 'Revoked'}</span></td>
                            <td className="py-3 text-right">
                              {key.is_active && (
                                <button type="button" onClick={() => handleRevoke(key)} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-brand-line text-brand-rose hover:bg-brand-rose/10" aria-label={`Revoke ${key.name}`} title="Revoke key"><Trash2 size={15} aria-hidden="true" /></button>
                              )}
                            </td>
                          </tr>
                        ))}
                        {!data.keys?.length && <tr><td colSpan="6" className="py-8 text-center text-sm text-brand-muted">No product keys yet.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <div className="rounded-xl border border-brand-line bg-brand-surface p-5">
                <div className="flex items-baseline justify-between gap-3"><p className="font-semibold text-brand-ink">Research tool reference</p><p className="text-xs text-brand-muted">{tools.length} published</p></div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  {TOOL_DOCS.filter(([name]) => tools.includes(name)).map(([name, description]) => (
                    <div key={name} className="border-l-2 border-brand-accent pl-3"><p className="font-mono text-xs font-semibold text-brand-ink">{name}</p><p className="mt-1 text-xs text-brand-muted">{description}</p></div>
                  ))}
                  {!tools.length && <p className="text-xs text-brand-muted">No Research tools are currently published.</p>}
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
