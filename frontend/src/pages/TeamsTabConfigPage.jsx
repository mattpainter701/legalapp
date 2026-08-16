import { useEffect, useState } from 'react'
import { app as teamsApp, pages as teamsPages } from '@microsoft/teams-js'
import { Check, MessageSquare, X } from 'lucide-react'
import {
  API_BASE_URL,
  createTeamsLink,
  getIntegrationStatus,
  getMatters,
  getTeamsChannels,
  getTeamsTeams,
} from '../api'

export default function TeamsTabConfigPage() {
  const [status, setStatus] = useState(null)
  const [teams, setTeams] = useState([])
  const [channels, setChannels] = useState([])
  const [matters, setMatters] = useState([])
  const [teamId, setTeamId] = useState('')
  const [channelId, setChannelId] = useState('')
  const [matterId, setMatterId] = useState('')
  const [saving, setSaving] = useState(false)
  const [flash, setFlash] = useState(null)
  const canSave = Boolean(teamId && channelId && matterId)

  useEffect(() => {
    teamsApp.initialize().catch(() => {})
    Promise.all([
      getIntegrationStatus(),
      getTeamsTeams().catch(() => []),
      getMatters().catch(() => []),
    ]).then(([integrationStatus, teamData, matterData]) => {
      setStatus(integrationStatus)
      setTeams(teamData || [])
      setMatters(matterData || [])
    })
  }, [])

  useEffect(() => {
    try {
      teamsPages.config.setValidityState(canSave)
    } catch {
      // Outside Teams, the page still works as a normal admin route.
    }
  }, [canSave])

  const handleTeamChange = async (value) => {
    setTeamId(value)
    setChannelId('')
    setChannels([])
    if (!value) return
    setChannels(await getTeamsChannels(value).catch(() => []))
  }

  const saveConfiguration = async () => {
    if (!canSave) return
    setSaving(true)
    setFlash(null)
    const team = teams.find((item) => item.id === teamId)
    const channel = channels.find((item) => item.id === channelId)
    try {
      await createTeamsLink({
        matter_id: matterId,
        team_id: teamId,
        channel_id: channelId,
        team_display_name: team?.display_name || '',
        channel_display_name: channel?.display_name || '',
      })
      setFlash({ type: 'success', text: 'Teams tab configured.' })
      const teamsSdk = window.microsoftTeams
      if (teamsSdk?.pages?.config?.setConfig) {
        const teamsUrl = `${window.location.origin}/teams`
        await teamsSdk.pages.config.setConfig({
          entityId: matterId,
          contentUrl: teamsUrl,
          websiteUrl: teamsUrl,
          suggestedDisplayName: 'LawHand',
        })
      }
    } catch (err) {
      setFlash({ type: 'error', text: err?.response?.data?.detail || 'Failed to configure Teams tab.' })
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    try {
      teamsPages.config.registerOnSaveHandler((saveEvent) => {
        saveConfiguration()
          .then(() => saveEvent.notifySuccess())
          .catch(() => saveEvent.notifyFailure('Failed to configure Teams tab.'))
      })
    } catch {
      // TeamsJS config handlers are available only in a Teams config surface.
    }
  }, [teamId, channelId, matterId, teams, channels, canSave])

  const reconnectTeams = () => {
    window.location.href = `${API_BASE_URL}/integrations/microsoft/connect?intent=admin&teams=1`
  }

  const ms = status?.microsoft

  return (
    <div className="min-h-screen bg-brand-bg text-brand-ink">
      <div className="max-w-3xl mx-auto px-5 py-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-lg bg-brand-ink text-white flex items-center justify-center">
            <MessageSquare className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-serif font-semibold text-xl">LawHand</h1>
            <p className="text-xs text-brand-muted">Teams channel setup</p>
          </div>
        </div>

        {flash && (
          <div className={`mb-4 px-4 py-3 rounded-lg border text-xs font-medium flex items-center gap-2 ${
            flash.type === 'success'
              ? 'bg-green-50 border-green-200 text-green-700'
              : 'bg-red-50 border-red-200 text-red-700'
          }`}>
            {flash.type === 'success' ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
            {flash.text}
          </div>
        )}

        {!ms?.teams_connected ? (
          <div className="border border-brand-line bg-brand-surface rounded-lg p-5">
            <h2 className="text-sm font-bold mb-2">Teams permissions required</h2>
            <button
              onClick={reconnectTeams}
              className="px-4 py-2 bg-brand-ink text-white text-xs font-medium rounded-lg hover:bg-brand-ink/90"
            >
              Connect Microsoft Teams
            </button>
          </div>
        ) : (
          <div className="border border-brand-line bg-brand-surface rounded-lg p-5 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <select
                value={teamId}
                onChange={(e) => handleTeamChange(e.target.value)}
                className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm"
              >
                <option value="">Team</option>
                {teams.map((team) => (
                  <option key={team.id} value={team.id}>{team.display_name}</option>
                ))}
              </select>
              <select
                value={channelId}
                onChange={(e) => setChannelId(e.target.value)}
                disabled={!teamId}
                className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm disabled:opacity-50"
              >
                <option value="">Channel</option>
                {channels.map((channel) => (
                  <option key={channel.id} value={channel.id}>{channel.display_name}</option>
                ))}
              </select>
              <select
                value={matterId}
                onChange={(e) => setMatterId(e.target.value)}
                className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm"
              >
                <option value="">Matter</option>
                {matters.map((matter) => (
                  <option key={matter.id} value={matter.id}>
                    {matter.matter_name || matter.name || matter.slug}
                  </option>
                ))}
              </select>
            </div>
            <button
              onClick={saveConfiguration}
              disabled={saving || !canSave}
              className="px-4 py-2 bg-brand-ink text-white text-xs font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Save configuration'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
