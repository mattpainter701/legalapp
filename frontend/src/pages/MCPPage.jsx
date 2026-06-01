import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getMcpInfo, regenerateMcpApiKey } from '../api'
import { useAuth } from '../App'

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false)
  const handle = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={handle}
      className="ml-2 text-xs text-brand-accent hover:underline font-sans"
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

function CodeBlock({ value, label }) {
  return (
    <div>
      {label && (
        <p className="text-xs text-brand-muted uppercase tracking-wider font-sans mb-1">{label}</p>
      )}
      <div className="flex items-center justify-between bg-brand-bg border border-brand-line rounded-lg px-3 py-2">
        <code className="text-sm text-brand-ink-2 font-mono truncate">{value}</code>
        <CopyButton value={value} />
      </div>
    </div>
  )
}

const TOOL_DOCS = [
  {
    name: 'search_caselaw',
    description: 'Semantic search across case law database',
    params: [
      { name: 'query', type: 'string', required: true, desc: 'Legal question or research query' },
      { name: 'top_k', type: 'integer', required: false, desc: 'Results to return (1–20, default 8)' },
    ],
  },
  {
    name: 'get_chunk',
    description: 'Retrieve full text of a case law chunk by ID',
    params: [
      { name: 'chunk_id', type: 'string', required: true, desc: 'UUID from search_caselaw results' },
    ],
  },
]

export default function MCPPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [info, setInfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [regenerating, setRegenerating] = useState(false)
  const [error, setError] = useState(null)
  const [newKey, setNewKey] = useState(null)

  useEffect(() => {
    getMcpInfo()
      .then(setInfo)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load MCP info'))
      .finally(() => setLoading(false))
  }, [])

  const handleRegenerate = async () => {
    if (!window.confirm('Regenerate API key? The old key will stop working immediately.')) return
    setRegenerating(true)
    try {
      const result = await regenerateMcpApiKey()
      setNewKey(result.api_key)
      setInfo((prev) => ({ ...prev, has_api_key: true, api_key_masked: result.api_key.slice(0, 8) + '...' + result.api_key.slice(-4) }))
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to regenerate key')
    } finally {
      setRegenerating(false)
    }
  }

  if (user?.role !== 'admin') {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <p className="text-brand-muted font-sans">Admin access required.</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="w-6 h-6 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="max-w-2xl mx-auto px-4 py-12">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-brand-ink font-serif">MCP Server</h1>
            <p className="text-sm text-brand-muted mt-1 font-sans">
              Connect Claude Desktop or any MCP-compatible client to Clarity Legal
            </p>
          </div>
          <button onClick={() => navigate(-1)} className="text-sm text-brand-muted hover:text-brand-ink font-sans">
            ← Back
          </button>
        </div>

        {error && (
          <div className="mb-6 bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-sm text-brand-rose font-sans">
            {error}
          </div>
        )}

        {/* Product description */}
        <div className="bg-brand-surface border border-brand-line rounded-xl p-6 shadow-sm mb-6">
          <h2 className="font-serif text-2xl font-bold text-brand-ink mb-3">Model Context Protocol (MCP)</h2>
          <p className="font-sans text-brand-muted text-sm leading-relaxed mb-4">
            Connect any MCP-compatible AI assistant — Claude, Cursor, or custom agents — directly to your
            legal knowledge base. Search case law, retrieve document chunks, and run legal skills from
            external tools without leaving your workflow.
          </p>
          <p className="font-sans text-brand-muted text-sm">
            MCP usage is billed through your existing plan.
          </p>
        </div>

        {/* Connection info */}
        <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6 mb-6 space-y-4">
          <p className="text-sm font-semibold text-brand-ink font-sans">Connection Details</p>

          {info?.mcp_server_url && (
            <CodeBlock label="MCP Server URL" value={info.mcp_server_url} />
          )}

          {newKey ? (
            <div>
              <p className="text-xs text-brand-muted uppercase tracking-wider font-sans mb-1">
                New API Key — copy now, won't be shown again
              </p>
              <div className="flex items-center justify-between bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                <code className="text-sm text-amber-800 font-mono break-all">{newKey}</code>
                <CopyButton value={newKey} />
              </div>
            </div>
          ) : info?.has_api_key ? (
            <div>
              <p className="text-xs text-brand-muted uppercase tracking-wider font-sans mb-1">API Key</p>
              <div className="flex items-center justify-between bg-brand-bg border border-brand-line rounded-lg px-3 py-2">
                <code className="text-sm text-brand-muted font-mono">{info.api_key_masked}</code>
                <button
                  onClick={handleRegenerate}
                  disabled={regenerating}
                  className="ml-2 text-xs text-brand-rose hover:underline font-sans"
                >
                  {regenerating ? 'Regenerating…' : 'Regenerate'}
                </button>
              </div>
            </div>
          ) : (
            <div>
              <p className="text-sm text-brand-muted font-sans mb-3">
                No API key yet. Generate one to authenticate MCP clients.
              </p>
              <button
                onClick={handleRegenerate}
                disabled={regenerating}
                className="bg-brand-accent text-white px-4 py-2 rounded-lg text-sm font-medium font-sans hover:bg-brand-accent-2 disabled:opacity-60"
              >
                {regenerating ? 'Generating…' : 'Generate API Key'}
              </button>
            </div>
          )}
        </div>

        {/* Claude Desktop config */}
        {info?.mcp_server_url && (
          <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6 mb-6">
            <p className="text-sm font-semibold text-brand-ink font-sans mb-3">
              Claude Desktop Config (claude_desktop_config.json)
            </p>
            <div className="bg-gray-900 rounded-lg p-4 overflow-auto">
              <pre className="text-xs text-green-300 font-mono whitespace-pre">{`{
  "mcpServers": {
    "clarity-legal": {
      "command": "curl",
      "args": [
        "-s", "-X", "POST",
        "${info.mcp_server_url}/tools/call",
        "-H", "Content-Type: application/json",
        "-H", "X-API-Key: YOUR_API_KEY_HERE"
      ]
    }
  }
}`}</pre>
            </div>
            <p className="text-xs text-brand-muted mt-2 font-sans">
              Replace <code className="bg-brand-bg-soft px-1 rounded">YOUR_API_KEY_HERE</code> with your API key above.
            </p>
          </div>
        )}

        {/* Tool reference */}
        <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6">
          <p className="text-sm font-semibold text-brand-ink font-sans mb-4">Available Tools</p>
          <div className="space-y-5">
            {TOOL_DOCS.map((tool) => (
              <div key={tool.name} className="border-l-2 border-brand-accent pl-4">
                <p className="text-sm font-mono font-semibold text-brand-ink">{tool.name}</p>
                <p className="text-xs text-brand-muted mt-0.5 font-sans mb-2">{tool.description}</p>
                <table className="text-xs font-sans w-full">
                  <thead>
                    <tr className="text-brand-muted">
                      <th className="text-left pb-1 pr-3">Param</th>
                      <th className="text-left pb-1 pr-3">Type</th>
                      <th className="text-left pb-1">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tool.params.map((p) => (
                      <tr key={p.name}>
                        <td className="pr-3 py-0.5 font-mono text-brand-ink-2">
                          {p.name}
                          {p.required && <span className="text-brand-rose ml-0.5">*</span>}
                        </td>
                        <td className="pr-3 py-0.5 text-brand-muted">{p.type}</td>
                        <td className="py-0.5 text-brand-muted">{p.desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
