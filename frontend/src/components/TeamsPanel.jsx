import React, { useEffect, useState } from 'react'
import {
  API_BASE_URL,
  getIntegrationStatus,
  getTeamsTeams,
  getTeamsChannels,
  createTeamsChannel,
  getTeamsLinks,
  createTeamsLink,
  deleteTeamsLink,
  sendTeamsTestMessage,
  getMattersV2,
} from '../api'

export default function TeamsPanel() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [teams, setTeams] = useState([])
  const [channels, setChannels] = useState([])
  const [matters, setMatters] = useState([])
  const [links, setLinks] = useState([])

  const [selTeam, setSelTeam] = useState('')
  const [selChannel, setSelChannel] = useState('')
  const [selMatter, setSelMatter] = useState('')
  const [newChannelName, setNewChannelName] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)

  const showFlash = (text, type = 'success') => {
    setFlash({ text, type })
    setTimeout(() => setFlash(null), 4000)
  }

  const loadPanel = async () => {
    try {
      const [t, l] = await Promise.all([getTeamsTeams(), getTeamsLinks()])
      setTeams(t)
      setLinks(l)
      try {
        const data = await getMattersV2({ page_size: 200 })
        setMatters(Array.isArray(data) ? data : (data.items || []))
      } catch (e) {
        showFlash(e?.response?.data?.detail || 'Failed to load matters.', 'error')
        setMatters([])
      }
    } catch {
      setError('Failed to load Teams data.')
    }
  }

  useEffect(() => {
    getIntegrationStatus()
      .then(async (s) => {
        setStatus(s)
        if (s?.microsoft?.teams_connected) {
          await loadPanel()
        }
      })
      .catch(() => setError('Failed to load integration status.'))
      .finally(() => setLoading(false))
  }, [])

  const handleTeamChange = async (teamId) => {
    setSelTeam(teamId)
    setSelChannel('')
    setChannels([])
    if (!teamId) return
    try {
      setChannels(await getTeamsChannels(teamId))
    } catch {
      showFlash('Failed to load channels.', 'error')
    }
  }

  const teamName = (id) => teams.find((t) => t.id === id)?.display_name || ''
  const channelName = (id) => channels.find((c) => c.id === id)?.display_name || ''
  const selectedMatter = matters.find((m) => m.id === selMatter)
  const matterLabel = (matter) => matter?.matter_name || matter?.name || matter?.slug || ''
  const defaultChannelName = () => {
    const base = matterLabel(selectedMatter) || 'Matter'
    return base.replace(/[~#%&*{}+/\\:<>?|"]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 50)
  }

  const handleLink = async () => {
    if (!selTeam || !selChannel || !selMatter) return
    setBusy(true)
    try {
      await createTeamsLink({
        matter_id: selMatter,
        team_id: selTeam,
        channel_id: selChannel,
        team_display_name: teamName(selTeam),
        channel_display_name: channelName(selChannel),
      })
      setLinks(await getTeamsLinks())
      showFlash('Matter linked to channel.')
    } catch (e) {
      showFlash(e?.response?.data?.detail || 'Failed to link.', 'error')
    } finally {
      setBusy(false)
    }
  }

  const handleCreateChannel = async () => {
    if (!selTeam) {
      showFlash('Pick a team first.', 'error')
      return
    }
    const displayName = (newChannelName || defaultChannelName()).trim()
    if (!displayName) {
      showFlash('Enter a channel name or select a matter.', 'error')
      return
    }
    setBusy(true)
    try {
      const channel = await createTeamsChannel({
        team_id: selTeam,
        display_name: displayName,
        description: selectedMatter
          ? `WellPled matter channel for ${matterLabel(selectedMatter)}`
          : 'WellPled matter channel',
      })
      const nextChannels = [...channels.filter((c) => c.id !== channel.id), channel]
      setChannels(nextChannels)
      setSelChannel(channel.id)
      setNewChannelName('')
      showFlash(`Created channel ${channel.display_name || displayName}.`)
    } catch (e) {
      const detail = e?.response?.data?.detail
      const message = typeof detail === 'string'
        ? detail
        : detail?.message || 'Failed to create channel. Reconnect Teams if Channel.Create consent is missing.'
      showFlash(message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const handleUnlink = async (id) => {
    try {
      await deleteTeamsLink(id)
      setLinks(links.filter((l) => l.id !== id))
    } catch {
      showFlash('Failed to unlink.', 'error')
    }
  }

  const handleTest = async () => {
    if (!selTeam || !selChannel) {
      showFlash('Pick a team and channel first.', 'error')
      return
    }
    setBusy(true)
    try {
      await sendTeamsTestMessage({ team_id: selTeam, channel_id: selChannel })
      showFlash('Test card sent to the channel.')
    } catch (e) {
      showFlash(e?.response?.data?.detail || 'Failed to send test message.', 'error')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="w-8 h-8 border-4 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const ms = status?.microsoft
  const reconnectTeams = () => {
    window.location.href = `${API_BASE_URL}/integrations/microsoft/connect?intent=admin&teams=1`
  }

  // ── Gated states ──────────────────────────────────────────────────────────
  if (!ms?.connected) {
    return (
      <GateCard
        title="Connect Microsoft 365 first"
        body="Microsoft Teams collaboration requires an active Microsoft 365 integration. Connect Microsoft in the Integrations tab, then return here."
      />
    )
  }

  if (!ms?.teams_connected) {
    return (
      <GateCard
        title="Enable Microsoft Teams"
        body="Your Microsoft 365 connection is missing the Teams permissions. Reconnect to grant channel and messaging access — your existing cloud features are unaffected."
        action={{ label: 'Reconnect to enable Teams', onClick: reconnectTeams }}
        missing={ms?.teams_missing_scopes}
      />
    )
  }

  // ── Connected panel ───────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-xs font-medium">{error}</div>
      )}
      {flash && (
        <div className={`px-4 py-3 rounded-xl text-xs font-medium ${flash.type === 'success' ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
          {flash.text}
        </div>
      )}

      <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border bg-green-100 text-green-700 border-green-200 text-sm font-bold">
        <span className="w-2 h-2 rounded-full bg-green-500" />
        Teams connected
      </div>

      {/* Link a matter to a channel */}
      <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
        <h3 className="text-brand-ink font-sans text-base font-bold mb-1">Link a matter to a Teams channel</h3>
        <p className="text-brand-ink-2 font-sans text-xs mb-4">
          Notifications for a matter (e.g. approaching deadlines) are posted as Adaptive Cards to every linked channel.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
          <select
            value={selTeam}
            onChange={(e) => handleTeamChange(e.target.value)}
            className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          >
            <option value="">Select team…</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>{t.display_name}</option>
            ))}
          </select>
          <select
            value={selChannel}
            onChange={(e) => setSelChannel(e.target.value)}
            disabled={!selTeam}
            className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          >
            <option value="">Select channel…</option>
            {channels.map((c) => (
              <option key={c.id} value={c.id}>{c.display_name}</option>
            ))}
          </select>
          <select
            value={selMatter}
            onChange={(e) => {
              const matterId = e.target.value
              setSelMatter(matterId)
              const matter = matters.find((m) => m.id === matterId)
              if (matter && !newChannelName) {
                setNewChannelName((matter.matter_name || matter.name || matter.slug || '').slice(0, 50))
              }
            }}
            className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          >
            <option value="">{matters.length ? 'Select matter…' : 'No matters found'}</option>
            {matters.map((m) => (
              <option key={m.id} value={m.id}>{m.matter_name || m.name || m.slug}</option>
            ))}
          </select>
        </div>
        <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-[1fr_auto]">
          <input
            value={newChannelName}
            onChange={(e) => setNewChannelName(e.target.value.slice(0, 50))}
            placeholder={selectedMatter ? `New channel: ${defaultChannelName()}` : 'New channel name'}
            disabled={!selTeam}
            className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          />
          <button
            onClick={handleCreateChannel}
            disabled={busy || !selTeam || (!newChannelName.trim() && !selectedMatter)}
            className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
          >
            Create channel
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleLink}
            disabled={busy || !selTeam || !selChannel || !selMatter}
            className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-medium rounded-lg hover:bg-brand-ink/90 transition-colors disabled:opacity-50"
          >
            Link channel
          </button>
          <button
            onClick={handleTest}
            disabled={busy || !selTeam || !selChannel}
            className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
          >
            Send test message
          </button>
          {matters.length === 0 && (
            <span className="text-xs text-brand-ink-2">
              No canonical matters loaded. Create or import a matter before linking Teams.
            </span>
          )}
        </div>
      </div>

      {/* Existing links */}
      <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
        <h3 className="text-brand-ink font-sans text-base font-bold mb-4">Linked channels</h3>
        {links.length === 0 ? (
          <p className="text-brand-ink-2 font-sans text-sm">No matters linked yet.</p>
        ) : (
          <div className="space-y-2">
            {links.map((l) => (
              <div key={l.id} className="flex items-center justify-between px-3 py-2 rounded-lg bg-brand-bg">
                <div className="text-sm text-brand-ink">
                  <span className="font-medium">{l.team_display_name || l.team_id}</span>
                  <span className="text-brand-ink-2"> / {l.channel_display_name || l.channel_id}</span>
                  <span className="text-brand-ink-2 text-xs ml-2">→ matter {l.matter_id.slice(0, 8)}</span>
                </div>
                <button
                  onClick={() => handleUnlink(l.id)}
                  className="text-xs text-red-500 hover:text-red-600 font-medium"
                >
                  Unlink
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function GateCard({ title, body, action, missing }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 max-w-2xl">
      <h3 className="text-brand-ink font-sans text-base font-bold mb-1">{title}</h3>
      <p className="text-brand-ink-2 font-sans text-sm mb-4">{body}</p>
      {missing && missing.length > 0 && (
        <p className="text-xs text-brand-ink-2 font-mono bg-brand-bg px-3 py-2 rounded-lg mb-4">
          Missing scopes: {missing.join(', ')}
        </p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-medium rounded-lg hover:bg-brand-ink/90 transition-colors"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
