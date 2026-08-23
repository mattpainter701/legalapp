import { useCallback, useEffect, useState } from 'react'
import { Bell, Check, MessageSquare, PhoneCall, X } from 'lucide-react'
import {
  API_BASE_URL,
  getIntegrationStatus,
  getMattersV2,
  getTeamsChannels,
  getTeamsEventTypes,
  getTeamsLinks,
  getTeamsNotificationSettings,
  getTeamsTeams,
  getTeamsVoiceStatus,
} from '../api'
import TeamsChannelsTab from './teams/TeamsChannelsTab'
import TeamsNotificationsTab from './teams/TeamsNotificationsTab'
import TeamsVoiceTab from './teams/TeamsVoiceTab'
import { errorText } from './teams/teamsErrors'

const TABS = [
  { key: 'channels', label: 'Channels', icon: MessageSquare },
  { key: 'notifications', label: 'Notifications', icon: Bell },
  { key: 'voice', label: 'Voice', icon: PhoneCall },
]

export default function TeamsPanel() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [tab, setTab] = useState('channels')

  const [teams, setTeams] = useState([])
  const [matters, setMatters] = useState([])
  const [links, setLinks] = useState([])
  const [eventTypes, setEventTypes] = useState([])
  const [notificationSettings, setNotificationSettings] = useState([])
  const [voiceStatus, setVoiceStatus] = useState(null)
  const [channelsByTeam, setChannelsByTeam] = useState({})

  const showFlash = useCallback((text, type = 'success') => {
    setFlash({ text, type })
    setTimeout(() => setFlash(null), 5000)
  }, [])

  const refreshLinks = useCallback(async () => {
    setLinks(await getTeamsLinks())
  }, [])

  // Channels are fetched per team and cached, so switching back and forth in
  // the pickers does not re-hit Graph (which throttles hard on Teams reads).
  const loadChannels = useCallback(async (teamId) => {
    if (!teamId) return []
    const channels = await getTeamsChannels(teamId)
    setChannelsByTeam((prev) => ({ ...prev, [teamId]: channels }))
    return channels
  }, [])

  const handleChannelCreated = useCallback((teamId, channel) => {
    setChannelsByTeam((prev) => ({
      ...prev,
      [teamId]: [...(prev[teamId] || []).filter((c) => c.id !== channel.id), channel],
    }))
  }, [])

  const loadPanel = useCallback(async () => {
    // Each source degrades independently: a Graph outage should not hide the
    // matter links and routing rules, which are all local data.
    const [teamsResult, linksResult, eventsResult, settingsResult, voiceResult] =
      await Promise.allSettled([
        getTeamsTeams(),
        getTeamsLinks(),
        getTeamsEventTypes(),
        getTeamsNotificationSettings(),
        getTeamsVoiceStatus(),
      ])

    if (teamsResult.status === 'fulfilled') {
      setTeams(teamsResult.value || [])
    } else {
      setTeams([])
      setError(
        errorText(
          teamsResult.reason,
          'Microsoft Graph could not list your teams. Reconnect Teams and try again.',
        ),
      )
    }
    if (linksResult.status === 'fulfilled') setLinks(linksResult.value || [])
    if (eventsResult.status === 'fulfilled') setEventTypes(eventsResult.value || [])
    if (settingsResult.status === 'fulfilled') {
      setNotificationSettings(settingsResult.value || [])
    }
    if (voiceResult.status === 'fulfilled') setVoiceStatus(voiceResult.value)

    try {
      const data = await getMattersV2({ page_size: 200 })
      setMatters(Array.isArray(data) ? data : data.items || [])
    } catch (err) {
      setMatters([])
      showFlash(errorText(err, 'Could not load matters.'), 'error')
    }
  }, [showFlash])

  useEffect(() => {
    getIntegrationStatus()
      .then(async (s) => {
        setStatus(s)
        if (s?.microsoft?.teams_connected) await loadPanel()
      })
      .catch((err) =>
        setError(errorText(err, 'Could not load integration status.')),
      )
      .finally(() => setLoading(false))
  }, [loadPanel])

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-brand-ink border-t-transparent" />
      </div>
    )
  }

  const ms = status?.microsoft
  const reconnectTeams = () => {
    window.location.href = `${API_BASE_URL}/integrations/microsoft/connect?intent=admin&teams=1`
  }

  if (!ms?.connected) {
    return (
      <GateCard
        title="Connect Microsoft 365 first"
        body="Teams collaboration builds on your Microsoft 365 connection. Connect Microsoft in the Integrations tab, then come back here."
      />
    )
  }

  if (!ms?.teams_connected) {
    return (
      <GateCard
        title="Enable Microsoft Teams"
        body="Your Microsoft 365 connection does not yet include Teams permissions. Reconnecting grants channel and messaging access — your existing cloud features are unaffected."
        action={{ label: 'Reconnect to enable Teams', onClick: reconnectTeams }}
        missing={ms?.teams_missing_scopes}
      />
    )
  }

  return (
    <div className="space-y-5">
      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 font-sans text-xs font-medium text-red-700">
          <X className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {flash && (
        <div
          className={`flex items-start gap-2 rounded-xl px-4 py-3 font-sans text-xs font-medium ${
            flash.type === 'success'
              ? 'border border-green-200 bg-green-50 text-green-700'
              : 'border border-red-200 bg-red-50 text-red-700'
          }`}
        >
          {flash.type === 'success' ? (
            <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          ) : (
            <X className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          )}
          <span>{flash.text}</span>
        </div>
      )}

      {/* Status strip: chat and voice are separately provisioned, so both
          states belong on screen at all times. */}
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone="good">Teams connected</Pill>
        <Pill tone={voiceStatus?.enabled ? 'good' : 'idle'}>
          {voiceStatus?.enabled
            ? voiceStatus?.subscription_active
              ? 'Voice capture live'
              : 'Voice capture on (hourly)'
            : 'Voice capture off'}
        </Pill>
        {links.length > 0 && (
          <Pill tone="idle">
            {links.length} linked matter{links.length === 1 ? '' : 's'}
          </Pill>
        )}
      </div>

      <div className="flex gap-1 border-b border-brand-line">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-4 py-2 font-sans text-xs font-bold transition-colors ${
              tab === key
                ? 'border-brand-ink text-brand-ink'
                : 'border-transparent text-brand-ink-2 hover:text-brand-ink'
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {tab === 'channels' && (
        <TeamsChannelsTab
          teams={teams}
          matters={matters}
          links={links}
          channelsByTeam={channelsByTeam}
          onLoadChannels={loadChannels}
          onLinksChanged={refreshLinks}
          onChannelCreated={handleChannelCreated}
          showFlash={showFlash}
        />
      )}

      {tab === 'notifications' && (
        <TeamsNotificationsTab
          eventTypes={eventTypes}
          settings={notificationSettings}
          teams={teams}
          matters={matters}
          channelsByTeam={channelsByTeam}
          onLoadChannels={loadChannels}
          onSaved={setNotificationSettings}
          showFlash={showFlash}
        />
      )}

      {tab === 'voice' && (
        <TeamsVoiceTab
          status={voiceStatus}
          onStatus={setVoiceStatus}
          showFlash={showFlash}
        />
      )}
    </div>
  )
}

function Pill({ tone, children }) {
  const cls =
    tone === 'good'
      ? 'bg-green-100 text-green-700 border-green-200'
      : 'bg-brand-bg-soft text-brand-ink-2 border-brand-line'
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 font-sans text-xs font-bold ${cls}`}
    >
      {tone === 'good' && <span className="h-1.5 w-1.5 rounded-full bg-green-500" />}
      {children}
    </span>
  )
}

function GateCard({ title, body, action, missing }) {
  return (
    <div className="max-w-2xl rounded-xl border border-brand-line bg-brand-surface p-6">
      <h3 className="mb-1 font-sans text-base font-bold text-brand-ink">{title}</h3>
      <p className="mb-4 font-sans text-sm text-brand-ink-2">{body}</p>
      {missing && missing.length > 0 && (
        <p className="mb-4 rounded-lg bg-brand-bg px-3 py-2 font-mono text-xs text-brand-ink-2">
          Missing permissions: {missing.join(', ')}
        </p>
      )}
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="rounded-lg bg-brand-ink px-4 py-2 font-sans text-xs font-medium text-white transition-colors hover:bg-brand-ink/90"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
