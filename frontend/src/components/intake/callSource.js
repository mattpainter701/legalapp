// Captured-call providers. Zoom Phone and Teams Phone land in the same intake
// feed, so the badge, the filter chip and the detail pane all read their
// labels from here instead of each hardcoding a single provider.
const SOURCES = {
  zoom_phone: { label: 'Zoom Phone', badge: 'Zoom', chip: 'Zoom' },
  teams_voice: { label: 'Microsoft Teams', badge: 'Teams', chip: 'Teams' },
}

export const isCapturedSource = (source) => Boolean(SOURCES[source])

export const callSourceLabel = (source) => SOURCES[source]?.label || 'Manual'

export const callSourceBadge = (source) => SOURCES[source]?.badge || null

export const callSourceChip = (source) =>
  source === 'all' ? 'All' : SOURCES[source]?.chip || source
