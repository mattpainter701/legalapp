import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import CalendarPage from './CalendarPage'

const api = vi.hoisted(() => ({
  connectCalendarIntegration: vi.fn(),
  connectZoomIntegration: vi.fn(),
  createScheduledEvent: vi.fn(),
  deleteScheduledEvent: vi.fn(),
  getCalendarEvents: vi.fn(),
  getCalendarProviders: vi.fn(),
  getMattersV2: vi.fn(),
  getZoomStatus: vi.fn(),
  syncCalendarDeadlines: vi.fn(),
  updateScheduledEvent: vi.fn(),
}))

vi.mock('../api', () => api)
vi.mock('../utils/reportError', () => ({ reportError: vi.fn() }))

const scheduledEvent = {
  id: 'scheduled-event-1',
  title: 'Calendar fixture',
  date: '2026-09-07',
  event_type: 'scheduled_event',
  start: '2026-09-07T13:30:00Z',
  end: '2026-09-07T14:00:00Z',
  calendar_provider: 'microsoft',
  meeting_provider: 'none',
  url: 'https://outlook.office.com/calendar/item-1',
}

async function renderCalendar() {
  render(<MemoryRouter><CalendarPage /></MemoryRouter>)
  await screen.findAllByText('Calendar fixture')
  fireEvent.click(screen.getAllByRole('button', { name: /Calendar fixture/ })[0])
  return screen.findByRole('dialog')
}

describe('scheduled calendar event details', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getCalendarEvents.mockResolvedValue({ events: [scheduledEvent] })
    api.getCalendarProviders.mockResolvedValue({ providers: [], provider_status: {}, tenant_providers: [] })
    api.getZoomStatus.mockResolvedValue({ connected: false, configured: false })
    api.getMattersV2.mockResolvedValue({ items: [] })
  })

  afterEach(cleanup)

  it('keeps the provider detail link and removes the displayed event after a successful delete', async () => {
    api.deleteScheduledEvent.mockResolvedValue({})
    await renderCalendar()

    expect(screen.getByRole('link', { name: 'Open in calendar' })).toHaveAttribute('href', scheduledEvent.url)
    fireEvent.click(screen.getByRole('button', { name: 'Delete event' }))

    await waitFor(() => expect(api.deleteScheduledEvent).toHaveBeenCalledWith('event-1'))
    expect(await screen.findByText('Event deleted from LawHand and its connected calendar.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('Calendar fixture')).not.toBeInTheDocument()
  })

  it('keeps the event visible and explains when connected-calendar deletion fails', async () => {
    api.deleteScheduledEvent.mockRejectedValue({ response: { data: { detail: 'Connected calendar unavailable' } } })
    await renderCalendar()

    fireEvent.click(screen.getByRole('button', { name: 'Delete event' }))

    expect(await screen.findByText('Connected calendar unavailable')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getAllByText('Calendar fixture').length).toBeGreaterThan(0)
  })
})
