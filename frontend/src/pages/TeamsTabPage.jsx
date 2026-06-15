import React, { useEffect, useState } from 'react'
import { CalendarDays, ExternalLink, Link2, MessageSquare } from 'lucide-react'
import { getTeamsLinks } from '../api'

export default function TeamsTabPage() {
  const [links, setLinks] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getTeamsLinks()
      .then((data) => setLinks(data || []))
      .catch(() => setLinks([]))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="min-h-screen bg-brand-bg text-brand-ink">
      <div className="max-w-4xl mx-auto px-5 py-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-lg bg-brand-ink text-white flex items-center justify-center">
            <MessageSquare className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-serif font-semibold text-xl">Clarity Legal</h1>
            <p className="text-xs text-brand-muted">Microsoft Teams</p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
          <a
            href="/matters"
            target="_blank"
            rel="noreferrer"
            className="border border-brand-line bg-brand-surface rounded-lg p-4 hover:bg-brand-bg-soft transition-colors"
          >
            <Link2 className="w-4 h-4 text-brand-accent mb-2" />
            <div className="text-sm font-bold">Matters</div>
            <div className="text-xs text-brand-muted mt-1">Open matter workspace</div>
          </a>
          <a
            href="/calendar"
            target="_blank"
            rel="noreferrer"
            className="border border-brand-line bg-brand-surface rounded-lg p-4 hover:bg-brand-bg-soft transition-colors"
          >
            <CalendarDays className="w-4 h-4 text-brand-accent mb-2" />
            <div className="text-sm font-bold">Calendar</div>
            <div className="text-xs text-brand-muted mt-1">Open deadlines and events</div>
          </a>
        </div>

        <div className="border border-brand-line bg-brand-surface rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold">Linked channels</h2>
            <a
              href="/teams/config"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-brand-ink hover:text-brand-accent"
            >
              Configure
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
          {loading ? (
            <div className="text-sm text-brand-muted py-4">Loading...</div>
          ) : links.length === 0 ? (
            <div className="text-sm text-brand-muted py-4">No channels linked.</div>
          ) : (
            <div className="space-y-2">
              {links.map((link) => (
                <div key={link.id} className="px-3 py-2 bg-brand-bg rounded-lg text-sm">
                  <span className="font-medium">{link.team_display_name || link.team_id}</span>
                  <span className="text-brand-muted"> / {link.channel_display_name || link.channel_id}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
