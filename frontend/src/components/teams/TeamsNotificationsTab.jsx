import { useEffect, useMemo, useState } from 'react'
import { Bell, Info, Save } from 'lucide-react'
import { updateTeamsNotificationSettings } from '../../api'
import { errorText, matterLabel } from './teamsErrors'

// One editable row per routable event. The catalogue comes from the server so
// this list and the code that actually fires notifications cannot drift.
export default function TeamsNotificationsTab({
  eventTypes,
  settings,
  teams,
  matters,
  channelsByTeam,
  onLoadChannels,
  onSaved,
  showFlash,
}) {
  const [rows, setRows] = useState({})
  const [saving, setSaving] = useState(false)

  // Existing routes are keyed by event; only the default (matter-wide) route is
  // editable here — per-matter overrides are created by linking a matter to a
  // channel on the Channels tab, which is the workflow admins actually use.
  useEffect(() => {
    const next = {}
    for (const event of eventTypes) {
      const existing = settings.find(
        (s) => s.event_type === event.event_type && !s.matter_id,
      )
      next[event.event_type] = {
        enabled: Boolean(existing?.is_enabled),
        teamId: existing?.team_id || '',
        channelId: existing?.channel_id || '',
      }
    }
    setRows(next)
  }, [eventTypes, settings])

  // Per-matter routes are preserved verbatim: the save endpoint replaces the
  // whole set, so anything this screen does not edit must be sent back as-is.
  const matterScopedRoutes = useMemo(
    () => settings.filter((s) => s.matter_id),
    [settings],
  )

  const update = (eventType, patch) =>
    setRows((prev) => ({ ...prev, [eventType]: { ...prev[eventType], ...patch } }))

  const handleTeamChange = async (eventType, teamId) => {
    update(eventType, { teamId, channelId: '' })
    if (teamId) await onLoadChannels(teamId)
  }

  const incomplete = Object.entries(rows).filter(
    ([, row]) => row.enabled && (!row.teamId || !row.channelId),
  )

  const save = async () => {
    if (incomplete.length) {
      showFlash('Pick a team and channel for every enabled event.', 'error')
      return
    }
    setSaving(true)
    const payload = [
      ...matterScopedRoutes.map((s) => ({
        event_type: s.event_type,
        team_id: s.team_id,
        channel_id: s.channel_id,
        team_display_name: s.team_display_name,
        channel_display_name: s.channel_display_name,
        matter_id: s.matter_id,
        is_enabled: s.is_enabled,
      })),
      ...Object.entries(rows)
        .filter(([, row]) => row.enabled && row.teamId && row.channelId)
        .map(([eventType, row]) => ({
          event_type: eventType,
          team_id: row.teamId,
          channel_id: row.channelId,
          team_display_name:
            teams.find((t) => t.id === row.teamId)?.display_name || null,
          channel_display_name:
            (channelsByTeam[row.teamId] || []).find((c) => c.id === row.channelId)
              ?.display_name || null,
          matter_id: null,
          is_enabled: true,
        })),
    ]
    try {
      const saved = await updateTeamsNotificationSettings(payload)
      onSaved(saved)
      showFlash('Notification routing saved.')
    } catch (err) {
      showFlash(errorText(err, 'Could not save notification routing.'), 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!eventTypes.length) {
    return (
      <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
        <p className="font-sans text-sm text-brand-ink-2">
          No routable events are available.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
        <div className="mb-1 flex items-center gap-2">
          <Bell className="h-4 w-4 text-brand-ink" />
          <h3 className="font-sans text-base font-bold text-brand-ink">
            Where events get posted
          </h3>
        </div>
        <p className="mb-5 font-sans text-xs leading-5 text-brand-ink-2">
          These are firm-wide defaults. A matter linked to its own channel on the
          Channels tab always posts there instead.
        </p>

        <div className="space-y-3">
          {eventTypes.map((event) => {
            const row = rows[event.event_type] || {}
            const channels = channelsByTeam[row.teamId] || []
            return (
              <div
                key={event.event_type}
                className="rounded-lg border border-brand-line bg-brand-bg p-4"
              >
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={Boolean(row.enabled)}
                    onChange={(e) =>
                      update(event.event_type, { enabled: e.target.checked })
                    }
                    className="mt-0.5 h-4 w-4 shrink-0 rounded border-brand-line"
                  />
                  <span className="min-w-0">
                    <span className="block font-sans text-sm font-bold text-brand-ink">
                      {event.label}
                    </span>
                    {event.description && (
                      <span className="mt-0.5 block font-sans text-xs text-brand-ink-2">
                        {event.description}
                      </span>
                    )}
                  </span>
                </label>

                {row.enabled && (
                  <div className="mt-3 grid grid-cols-1 gap-2 pl-7 sm:grid-cols-2">
                    <select
                      value={row.teamId || ''}
                      onChange={(e) =>
                        handleTeamChange(event.event_type, e.target.value)
                      }
                      className="rounded-lg border border-brand-line bg-brand-surface px-3 py-2 font-sans text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
                    >
                      <option value="">Select team…</option>
                      {teams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.display_name}
                        </option>
                      ))}
                    </select>
                    <select
                      value={row.channelId || ''}
                      onChange={(e) =>
                        update(event.event_type, { channelId: e.target.value })
                      }
                      disabled={!row.teamId}
                      className="rounded-lg border border-brand-line bg-brand-surface px-3 py-2 font-sans text-sm text-brand-ink disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
                    >
                      <option value="">
                        {row.teamId ? 'Select channel…' : 'Pick a team first'}
                      </option>
                      {channels.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.display_name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-ink px-4 py-2 font-sans text-xs font-medium text-white transition-colors hover:bg-brand-ink/90 disabled:opacity-50"
          >
            <Save className="h-3.5 w-3.5" />
            {saving ? 'Saving…' : 'Save routing'}
          </button>
          {incomplete.length > 0 && (
            <span className="font-sans text-xs text-amber-700">
              {incomplete.length} enabled event(s) still need a channel.
            </span>
          )}
        </div>
      </div>

      {matterScopedRoutes.length > 0 && (
        <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
          <div className="mb-1 flex items-center gap-2">
            <Info className="h-4 w-4 text-brand-ink-2" />
            <h3 className="font-sans text-sm font-bold text-brand-ink">
              Matter-specific overrides
            </h3>
          </div>
          <p className="mb-3 font-sans text-xs text-brand-ink-2">
            These take precedence over the defaults above and are preserved when you
            save.
          </p>
          <div className="space-y-2">
            {matterScopedRoutes.map((s) => (
              <div
                key={s.id}
                className="rounded-lg bg-brand-bg px-3 py-2 font-sans text-sm text-brand-ink"
              >
                <span className="font-medium">{s.event_type}</span>
                <span className="text-brand-ink-2">
                  {' '}
                  → {s.team_display_name || s.team_id} /{' '}
                  {s.channel_display_name || s.channel_id}
                </span>
                <span className="ml-2 font-sans text-xs text-brand-ink-2">
                  for{' '}
                  {s.matter_name ||
                    matterLabel(matters.find((m) => m.id === s.matter_id)) ||
                    'a matter'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
