import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Copy, KeyRound, Plus, RotateCcw, Trash2 } from 'lucide-react'
import {
  createMcpProductKey,
  getMcpProductKeys,
  revokeMcpProductKey,
} from '../api'
import { useAuth } from '../App'

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false)
  const handle = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    })
  }
  return (
    <button
      type="button"
      onClick={handle}
      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-brand-line text-brand-muted hover:text-brand-ink"
      title={copied ? 'Copied' : 'Copy'}
    >
      <Copy size={15} />
    </button>
  )
}

function CodeBlock({ value, label }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase text-brand-muted">{label}</p>
      <div className="flex items-center gap-2 rounded-lg border border-brand-line bg-brand-bg px-3 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-sm text-brand-ink-2">{value}</code>
        <CopyButton value={value} />
      </div>
    </div>
  )
}

const TOOL_DOCS = [
  ['search_caselaw', 'Hybrid vector and keyword search across CourtListener authority'],
  ['get_case_details', 'Fetch opinion and docket metadata for a case'],
  ['search_by_citation', 'Resolve a citation into local CourtListener authority'],
  ['get_citation_network', 'Inspect bounded local citation relationships'],
  ['search_by_jurisdiction', 'Filter authority by court or jurisdiction'],
  ['search_recent_authority', 'Find newer authority in the local corpus'],
  ['get_court_info', 'Return court metadata'],
]

export default function MCPPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [newKey, setNewKey] = useState(null)
  const [form, setForm] = useState({
    name: 'CourtListener API',
    monthly_call_limit: '',
    allowed_tools: ['search_caselaw'],
  })

  const tools = useMemo(() => data?.tools?.length ? data.tools : TOOL_DOCS.map(([name]) => name), [data])

  const load = () => {
    setLoading(true)
    getMcpProductKeys()
      .then(setData)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load MCP keys'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const toggleTool = (tool) => {
    setForm((prev) => {
      const exists = prev.allowed_tools.includes(tool)
      return {
        ...prev,
        allowed_tools: exists
          ? prev.allowed_tools.filter((item) => item !== tool)
          : [...prev.allowed_tools, tool],
      }
    })
  }

  const handleCreate = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const result = await createMcpProductKey({
        name: form.name,
        monthly_call_limit: form.monthly_call_limit ? Number(form.monthly_call_limit) : null,
        allowed_tools: form.allowed_tools.length ? form.allowed_tools : tools,
      })
      setNewKey(result.api_key)
      setForm({ name: 'CourtListener API', monthly_call_limit: '', allowed_tools: ['search_caselaw'] })
      await getMcpProductKeys().then(setData)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to create MCP key')
    } finally {
      setSaving(false)
    }
  }

  const handleRevoke = async (key) => {
    if (!window.confirm(`Revoke ${key.name}? Existing clients using this key will stop working.`)) return
    setError(null)
    try {
      await revokeMcpProductKey(key.id)
      await getMcpProductKeys().then(setData)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to revoke MCP key')
    }
  }

  if (user?.role !== 'admin') {
    return (
      <div className="flex h-screen items-center justify-center bg-brand-bg">
        <p className="font-sans text-brand-muted">Admin access required.</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-brand-bg">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-brand-accent border-t-transparent" />
      </div>
    )
  }

  const usage = data?.usage || { total_calls: 0, total_results: 0 }
  const transports = data?.transports || {}

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="mx-auto max-w-5xl px-4 py-10">
        <div className="mb-8 flex items-center justify-between gap-4">
          <div>
            <h1 className="font-serif text-2xl font-bold text-brand-ink">CourtListener MCP</h1>
            <p className="mt-1 text-sm text-brand-muted">
              Tenant-managed API keys for external MCP clients and sold access.
            </p>
          </div>
          <button onClick={() => navigate(-1)} className="text-sm text-brand-muted hover:text-brand-ink">
            Back
          </button>
        </div>

        {error && (
          <div className="mb-6 rounded-lg border border-brand-rose/20 bg-brand-rose/10 px-4 py-3 text-sm text-brand-rose">
            {error}
          </div>
        )}

        {newKey && (
          <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4">
            <p className="mb-2 text-sm font-semibold text-amber-900">New API key, copy now</p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded-md bg-white px-3 py-2 font-mono text-sm text-amber-900">
                {newKey}
              </code>
              <CopyButton value={newKey} />
            </div>
          </div>
        )}

        <div className="mb-6 grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border border-brand-line bg-brand-surface p-4">
            <p className="text-xs font-semibold uppercase text-brand-muted">30-day calls</p>
            <p className="mt-2 text-3xl font-semibold text-brand-ink">{usage.total_calls || 0}</p>
          </div>
          <div className="rounded-lg border border-brand-line bg-brand-surface p-4">
            <p className="text-xs font-semibold uppercase text-brand-muted">Returned results</p>
            <p className="mt-2 text-3xl font-semibold text-brand-ink">{usage.total_results || 0}</p>
          </div>
          <div className="rounded-lg border border-brand-line bg-brand-surface p-4">
            <p className="text-xs font-semibold uppercase text-brand-muted">Active keys</p>
            <p className="mt-2 text-3xl font-semibold text-brand-ink">
              {(data?.keys || []).filter((key) => key.is_active).length}
            </p>
          </div>
        </div>

        <div className="mb-6 rounded-lg border border-brand-line bg-brand-surface p-5">
          <p className="mb-4 text-sm font-semibold text-brand-ink">Connection endpoints</p>
          <div className="grid gap-3 md:grid-cols-2">
            <CodeBlock label="REST tool call" value={transports.rest || `${data?.mcp_server_url}/tools/call`} />
            <CodeBlock label="JSON-RPC messages" value={transports.messages || `${data?.mcp_server_url}/messages`} />
            <CodeBlock label="SSE discovery" value={transports.sse || `${data?.mcp_server_url}/sse`} />
            <CodeBlock label="Auth header" value="X-MCP-API-Key: clmcp_..." />
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
          <form onSubmit={handleCreate} className="rounded-lg border border-brand-line bg-brand-surface p-5">
            <div className="mb-4 flex items-center gap-2">
              <KeyRound size={18} className="text-brand-accent" />
              <p className="text-sm font-semibold text-brand-ink">Create product key</p>
            </div>

            <label className="mb-3 block">
              <span className="mb-1 block text-xs font-semibold uppercase text-brand-muted">Name</span>
              <input
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink"
                required
              />
            </label>

            <label className="mb-4 block">
              <span className="mb-1 block text-xs font-semibold uppercase text-brand-muted">Monthly call limit</span>
              <input
                type="number"
                min="1"
                value={form.monthly_call_limit}
                onChange={(e) => setForm((prev) => ({ ...prev, monthly_call_limit: e.target.value }))}
                placeholder="Unlimited"
                className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink"
              />
            </label>

            <p className="mb-2 text-xs font-semibold uppercase text-brand-muted">Allowed tools</p>
            <div className="mb-5 space-y-2">
              {tools.map((tool) => (
                <label key={tool} className="flex items-center gap-2 text-sm text-brand-ink">
                  <input
                    type="checkbox"
                    checked={form.allowed_tools.includes(tool)}
                    onChange={() => toggleTool(tool)}
                    className="h-4 w-4 rounded border-brand-line"
                  />
                  <span className="font-mono text-xs">{tool}</span>
                </label>
              ))}
            </div>

            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-md bg-brand-accent px-4 py-2 text-sm font-medium text-white hover:bg-brand-accent-2 disabled:opacity-60"
            >
              <Plus size={16} />
              {saving ? 'Creating' : 'Create key'}
            </button>
          </form>

          <div className="rounded-lg border border-brand-line bg-brand-surface p-5">
            <div className="mb-4 flex items-center justify-between">
              <p className="text-sm font-semibold text-brand-ink">Keys and usage</p>
              <button
                type="button"
                onClick={load}
                className="inline-flex items-center gap-2 rounded-md border border-brand-line px-3 py-2 text-sm text-brand-muted hover:text-brand-ink"
              >
                <RotateCcw size={15} />
                Refresh
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase text-brand-muted">
                  <tr>
                    <th className="pb-2 pr-3">Name</th>
                    <th className="pb-2 pr-3">Key</th>
                    <th className="pb-2 pr-3">Calls</th>
                    <th className="pb-2 pr-3">Limit</th>
                    <th className="pb-2 pr-3">Status</th>
                    <th className="pb-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.keys || []).map((key) => (
                    <tr key={key.id} className="border-t border-brand-line">
                      <td className="py-3 pr-3 font-medium text-brand-ink">{key.name}</td>
                      <td className="py-3 pr-3 font-mono text-xs text-brand-muted">{key.api_key_masked}</td>
                      <td className="py-3 pr-3 text-brand-ink">{key.usage?.calls || 0}</td>
                      <td className="py-3 pr-3 text-brand-muted">{key.monthly_call_limit || 'Unlimited'}</td>
                      <td className="py-3 pr-3">
                        <span className={key.is_active ? 'text-emerald-700' : 'text-brand-muted'}>
                          {key.is_active ? 'Active' : 'Revoked'}
                        </span>
                      </td>
                      <td className="py-3 text-right">
                        {key.is_active && (
                          <button
                            type="button"
                            onClick={() => handleRevoke(key)}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-brand-line text-brand-rose hover:bg-brand-rose/10"
                            title="Revoke key"
                          >
                            <Trash2 size={15} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!data?.keys?.length && (
                    <tr>
                      <td colSpan="6" className="py-8 text-center text-sm text-brand-muted">
                        No product keys yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-lg border border-brand-line bg-brand-surface p-5">
          <p className="mb-3 text-sm font-semibold text-brand-ink">Tool reference</p>
          <div className="grid gap-3 md:grid-cols-2">
            {TOOL_DOCS.map(([name, description]) => (
              <div key={name} className="border-l-2 border-brand-accent pl-3">
                <p className="font-mono text-xs font-semibold text-brand-ink">{name}</p>
                <p className="mt-1 text-xs text-brand-muted">{description}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

