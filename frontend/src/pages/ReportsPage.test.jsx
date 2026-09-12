import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReportsPage from './ReportsPage'

const { getRealizationReport, getWipReport, getAgingReport, getReportsBundle, downloadWipCsv } =
  vi.hoisted(() => ({
    getReportsBundle: vi.fn().mockResolvedValue({
      matter_status: { total_matters: 3, by_status: { open: 3 }, by_type: {}, by_risk_level: {} },
      intake_funnel: { total_leads: 4, conversion_rate: 0.25, by_status: {} },
      overdue_tasks: { total_overdue: 1, tasks: [{ id: 't1', title: 'File response', due_date: '2026-09-01', matter_name: 'Acme' }] },
      generated_at: '2026-09-12T10:00:00Z',
    }),
    getRealizationReport: vi.fn().mockResolvedValue([
      {
        matter_id: 'matter-1',
        matter_name: 'Acme matter',
        billable_hours: 10,
        billable_amount: 1000,
        invoiced_amount: 800,
        collected_amount: 400,
        billing_realization_pct: 80,
        collection_pct: 50,
        realization_pct: 40,
      },
    ]),
    getWipReport: vi.fn().mockResolvedValue([]),
    getAgingReport: vi.fn().mockResolvedValue([
      {
        matter_id: 'matter-1',
        matter_name: 'Acme matter',
        current: 500,
        days_1_30: 250,
        days_31_60: 0,
        days_61_90: 0,
        days_90_plus: 0,
        total: 750,
      },
    ]),
    downloadWipCsv: vi.fn().mockResolvedValue(new Blob(['matter_id\n'])),
  }))

vi.mock('../api', () => ({
  getReportsBundle,
  getRealizationReport,
  getWipReport,
  getAgingReport,
  downloadRealizationCsv: vi.fn(),
  downloadWipCsv,
  downloadAgingCsv: vi.fn(),
  triggerBlobDownload: vi.fn(),
}))

afterEach(() => { cleanup(); vi.clearAllMocks() })

const renderPage = () => render(<MemoryRouter><ReportsPage /></MemoryRouter>)

describe('ReportsPage', () => {
  it('separates billed from collected on the realization report', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Realization' }))

    const table = await screen.findByRole('table')
    expect(within(table).getByText('Invoiced')).toBeInTheDocument()
    // The data row plus the totals row both carry the figure.
    expect(within(table).getAllByText('$800.00').length).toBeGreaterThan(0)
    // Billed 80% of the work, collected 50% of what was billed.
    expect(within(table).getByText('80.0%')).toBeInTheDocument()
    expect(within(table).getByText('50.0%')).toBeInTheDocument()
  })

  it('shows a Current column so not-yet-due invoices are not read as late', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'A/R Aging' }))

    const table = await screen.findByRole('table')
    expect(within(table).getByText('Current')).toBeInTheDocument()
    expect(within(table).getAllByText('$500.00').length).toBeGreaterThan(0)
    expect(within(table).getAllByText('$250.00').length).toBeGreaterThan(0)
    expect(within(table).getAllByText('$750.00').length).toBeGreaterThan(0)
    // Totals row so nobody adds a page of money by hand.
    expect(within(table).getByText('Total')).toBeInTheDocument()
  })

  it('requests a date window when a period preset is chosen', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Realization' }))
    await waitFor(() => expect(getRealizationReport).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: 'Year to date' }))
    await waitFor(() => {
      const lastCall = getRealizationReport.mock.calls.at(-1)[0]
      expect(lastCall.start).toMatch(/^\d{4}-01-01$/)
      expect(lastCall.end).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
  })

  it('links a matter row through to its billing tab', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Realization' }))

    const link = await screen.findByRole('link', { name: 'Acme matter' })
    expect(link).toHaveAttribute('href', '/matters/matter-1?tab=billing')
  })

  it('offers a retry when a report fails to load', async () => {
    const user = userEvent.setup()
    getWipReport.mockRejectedValueOnce(new Error('Network down'))
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'WIP' }))

    expect(await screen.findByText('Network down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(getWipReport).toHaveBeenCalledTimes(2))
  })

  it('marks the sorted column for assistive technology', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Realization' }))

    const header = await screen.findByRole('columnheader', { name: /Worked Hours/ })
    expect(header).toHaveAttribute('aria-sort', 'none')
    await user.click(within(header).getByRole('button'))
    await waitFor(() => expect(header).toHaveAttribute('aria-sort', 'ascending'))
  })
})
