import { useState } from 'react'
import { Link2, Plus, Send, Trash2 } from 'lucide-react'
import {
  createTeamsChannel,
  createTeamsLink,
  deleteTeamsLink,
  sendTeamsTestMessage,
} from '../../api'
import { errorText, matterLabel } from './teamsErrors'

// Teams rejects these in a channel name, so strip them before we ever ask.
const CHANNEL_NAME_ILLEGAL = /[~#%&*{}+/\\:<>?|"]/g

const suggestedChannelName = (matter) =>
  matterLabel(matter)
    .replace(CHANNEL_NAME_ILLEGAL, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 50)

export default function TeamsChannelsTab({
  teams,
  matters,
  links,
  channelsByTeam,
  onLoadChannels,
  onLinksChanged,
  onChannelCreated,
  showFlash,
}) {
  const [teamId, setTeamId] = useState('')
  const [channelId, setChannelId] = useState('')
  const [matterId, setMatterId] = useState('')
  const [newChannelName, setNewChannelName] = useState('')
  const [busy, setBusy] = useState(null)
  // Unlinking silently stops a matter's notifications, so it asks once —
  // inline rather than through a native dialog, matching the rest of the app.
  const [confirmingUnlink, setConfirmingUnlink] = useState(null)

  const channels = channelsByTeam[teamId] || []
  const selectedMatter = matters.find((m) => m.id === matterId)
  const teamName = (id) => teams.find((t) => t.id === id)?.display_name || ''
  const channelName = (id) => channels.find((c) => c.id === id)?.display_name || ''

  const handleTeamChange = async (nextTeamId) => {
    setTeamId(nextTeamId)
    setChannelId('')
    if (!nextTeamId) return
    setBusy('channels')
    try {
      await onLoadChannels(nextTeamId)
    } catch (err) {
      showFlash(errorText(err, 'Could not load channels for that team.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  const handleMatterChange = (nextMatterId) => {
    setMatterId(nextMatterId)
    const matter = matters.find((m) => m.id === nextMatterId)
    if (matter && !newChannelName) setNewChannelName(suggestedChannelName(matter))
  }

  const handleCreateChannel = async () => {
    const displayName = (
      newChannelName || suggestedChannelName(selectedMatter)
    ).trim()
    if (!displayName) {
      showFlash('Name the channel, or pick a matter to name it after.', 'error')
      return
    }
    setBusy('create')
    try {
      const channel = await createTeamsChannel({
        team_id: teamId,
        display_name: displayName,
        description: selectedMatter
          ? `LawHand matter channel for ${matterLabel(selectedMatter)}`
          : 'LawHand matter channel',
      })
      onChannelCreated(teamId, channel)
      setChannelId(channel.id)
      setNewChannelName('')
      showFlash(`Created channel ${channel.display_name || displayName}.`)
    } catch (err) {
      showFlash(errorText(err, 'Could not create the channel.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  const handleLink = async () => {
    setBusy('link')
    try {
      await createTeamsLink({
        matter_id: matterId,
        team_id: teamId,
        channel_id: channelId,
        team_display_name: teamName(teamId),
        channel_display_name: channelName(channelId),
      })
      await onLinksChanged()
      setMatterId('')
      showFlash('Matter linked. Its notifications now post to that channel.')
    } catch (err) {
      showFlash(errorText(err, 'Could not link the matter.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  const handleUnlink = async (link) => {
    if (confirmingUnlink !== link.id) {
      setConfirmingUnlink(link.id)
      return
    }
    setConfirmingUnlink(null)
    setBusy(`unlink:${link.id}`)
    try {
      await deleteTeamsLink(link.id)
      await onLinksChanged()
      showFlash('Matter unlinked. It no longer posts to that channel.')
    } catch (err) {
      showFlash(errorText(err, 'Could not unlink the matter.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  const handleTest = async () => {
    setBusy('test')
    try {
      await sendTeamsTestMessage({
        team_id: teamId,
        channel_id: channelId,
        matter_name: matterLabel(selectedMatter) || 'Test Matter',
      })
      showFlash(`Test card posted to ${channelName(channelId) || 'the channel'}.`)
    } catch (err) {
      showFlash(errorText(err, 'Could not post the test message.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
        <h3 className="font-sans text-base font-bold text-brand-ink">
          Link a matter to a channel
        </h3>
        <p className="mb-5 mt-1 font-sans text-xs leading-5 text-brand-ink-2">
          Everything LawHand posts about a matter — approaching deadlines, captured
          calls — goes to every channel it is linked to, as an Adaptive Card.
        </p>

        <div className="space-y-4">
          <Field step="1" label="Team">
            <select
              value={teamId}
              onChange={(e) => handleTeamChange(e.target.value)}
              className="w-full rounded-lg border border-brand-line bg-brand-bg px-3 py-2 font-sans text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            >
              <option value="">
                {teams.length ? 'Select team…' : 'No teams found'}
              </option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.display_name}
                </option>
              ))}
            </select>
          </Field>

          <Field
            step="2"
            label="Channel"
            hint={teamId ? 'Pick an existing channel, or create one below.' : null}
          >
            <select
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              disabled={!teamId || busy === 'channels'}
              className="w-full rounded-lg border border-brand-line bg-brand-bg px-3 py-2 font-sans text-sm text-brand-ink disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            >
              <option value="">
                {!teamId
                  ? 'Pick a team first'
                  : busy === 'channels'
                    ? 'Loading channels…'
                    : channels.length
                      ? 'Select channel…'
                      : 'No channels in this team'}
              </option>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name}
                </option>
              ))}
            </select>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto]">
              <input
                value={newChannelName}
                onChange={(e) => setNewChannelName(e.target.value.slice(0, 50))}
                placeholder={
                  selectedMatter
                    ? `New channel: ${suggestedChannelName(selectedMatter)}`
                    : 'New channel name'
                }
                disabled={!teamId}
                className="rounded-lg border border-brand-line bg-brand-bg px-3 py-2 font-sans text-sm text-brand-ink disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
              />
              <button
                type="button"
                onClick={handleCreateChannel}
                disabled={
                  busy === 'create' ||
                  !teamId ||
                  (!newChannelName.trim() && !selectedMatter)
                }
                className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-brand-line px-4 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" />
                {busy === 'create' ? 'Creating…' : 'Create channel'}
              </button>
            </div>
          </Field>

          <Field step="3" label="Matter">
            <select
              value={matterId}
              onChange={(e) => handleMatterChange(e.target.value)}
              className="w-full rounded-lg border border-brand-line bg-brand-bg px-3 py-2 font-sans text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            >
              <option value="">
                {matters.length ? 'Select matter…' : 'No matters found'}
              </option>
              {matters.map((m) => (
                <option key={m.id} value={m.id}>
                  {matterLabel(m)}
                </option>
              ))}
            </select>
            {matters.length === 0 && (
              <p className="mt-1 font-sans text-xs text-brand-ink-2">
                Create or import a matter before linking it to Teams.
              </p>
            )}
          </Field>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleLink}
            disabled={busy === 'link' || !teamId || !channelId || !matterId}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-ink px-4 py-2 font-sans text-xs font-medium text-white transition-colors hover:bg-brand-ink/90 disabled:opacity-50"
          >
            <Link2 className="h-3.5 w-3.5" />
            {busy === 'link' ? 'Linking…' : 'Link matter to channel'}
          </button>
          <button
            type="button"
            onClick={handleTest}
            disabled={busy === 'test' || !teamId || !channelId}
            className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-4 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" />
            {busy === 'test' ? 'Sending…' : 'Send a test card'}
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
        <h3 className="mb-4 font-sans text-base font-bold text-brand-ink">
          Linked matters{links.length > 0 && ` (${links.length})`}
        </h3>
        {links.length === 0 ? (
          <p className="font-sans text-sm text-brand-ink-2">
            Nothing linked yet. Matters you link above appear here.
          </p>
        ) : (
          <div className="space-y-2">
            {links.map((link) => (
              <div
                key={link.id}
                className="flex items-center justify-between gap-3 rounded-lg bg-brand-bg px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="truncate font-sans text-sm font-medium text-brand-ink">
                    {link.matter_name || 'Unknown matter'}
                  </div>
                  <div className="truncate font-sans text-xs text-brand-ink-2">
                    {link.team_display_name || link.team_id} ·{' '}
                    {link.channel_display_name || link.channel_id}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleUnlink(link)}
                  onBlur={() =>
                    setConfirmingUnlink((current) =>
                      current === link.id ? null : current,
                    )
                  }
                  disabled={busy === `unlink:${link.id}`}
                  className={`inline-flex shrink-0 items-center gap-1 font-sans text-xs font-medium transition-colors disabled:opacity-50 ${
                    confirmingUnlink === link.id
                      ? 'text-red-600'
                      : 'text-red-500 hover:text-red-600'
                  }`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {busy === `unlink:${link.id}`
                    ? 'Removing…'
                    : confirmingUnlink === link.id
                      ? 'Confirm unlink'
                      : 'Unlink'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ step, label, hint, children }) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="font-sans text-[10px] font-bold text-brand-ink-2">{step}</span>
        <span className="font-sans text-xs font-bold text-brand-ink">{label}</span>
        {hint && <span className="font-sans text-[11px] text-brand-ink-2">{hint}</span>}
      </div>
      {children}
    </div>
  )
}
