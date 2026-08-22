import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DomesticPortfolioPage from './DomesticPortfolioPage'
import EstatePortfolioPage from './EstatePortfolioPage'
import InvoicesPage from './InvoicesPage'
import ProfilePage from './ProfilePage'
import TrustAccountingPage from './TrustAccountingPage'

vi.mock('../App', () => ({
  useAuth: () => ({ user: { full_name: 'Test User', email: 'test@example.com', role: 'user' }, refreshUser: vi.fn() }),
}))

vi.mock('../api', () => ({
  createDomesticCase: vi.fn(),
  createEstate: vi.fn(),
  createTrustAccount: vi.fn(),
  generateInvoice: vi.fn(),
  getDomesticCases: vi.fn().mockResolvedValue([
    { id: 'domestic-1', case_name: 'Rivera custody', case_type: 'custody', status: 'active' },
  ]),
  getEstates: vi.fn().mockResolvedValue([
    { id: 'estate-1', estate_name: 'Morgan Estate', estate_type: 'probate', status: 'active' },
  ]),
  getInvoices: vi.fn().mockResolvedValue({
    items: [{
      id: 'invoice-1',
      invoice_number: 'INV-1042',
      status: 'sent',
      total: 250,
      balance_due: 250,
      qbo_sync_status: 'pending',
    }],
  }),
  getMyMatters: vi.fn().mockResolvedValue([
    { id: 'profile-matter-1', matter_name: 'Acme advisory', matter_type: 'commercial', status: 'active' },
  ]),
  getAppVersion: vi.fn().mockResolvedValue({ version: 'test', release_notes: [] }),
  getMattersV2: vi.fn().mockResolvedValue({ items: [] }),
  getTimeEntries: vi.fn().mockResolvedValue({ items: [] }),
  updateMe: vi.fn().mockResolvedValue({}),
  getWorkspaceMcpGrants: vi.fn().mockResolvedValue({ items: [] }),
  revokeWorkspaceMcpGrant: vi.fn().mockResolvedValue({}),
  listTrustAccounts: vi.fn().mockResolvedValue({
    items: [{ id: 'trust-1', account_name: 'Rivera Client Trust', current_balance: 500, is_active: true }],
    total_balance: 500,
  }),
}))

function CurrentPath() {
  return <output aria-label="Current path">{useLocation().pathname}</output>
}

function renderPage(page) {
  return render(
    <MemoryRouter initialEntries={['/start']}>
      {page}
      <CurrentPath />
    </MemoryRouter>,
  )
}

describe('clickable table-row keyboard navigation', () => {
  afterEach(() => cleanup())

  it.each([
    {
      name: 'domestic case',
      page: <DomesticPortfolioPage />,
      accessibleName: 'Rivera custody',
      expectedPath: '/plugins/domestic/cases/domestic-1',
    },
    {
      name: 'estate',
      page: <EstatePortfolioPage />,
      accessibleName: 'Morgan Estate',
      expectedPath: '/plugins/trust-estate/estates/estate-1',
    },
    {
      name: 'invoice',
      page: <InvoicesPage />,
      accessibleName: 'INV-1042',
      expectedPath: '/invoices/invoice-1',
    },
    {
      name: 'profile matter',
      page: <ProfilePage />,
      accessibleName: 'Acme advisory',
      expectedPath: '/matters/profile-matter-1',
    },
    {
      name: 'trust account',
      page: <TrustAccountingPage />,
      accessibleName: 'Rivera Client Trust',
      expectedPath: '/trust/trust-1',
    },
  ])('opens the $name from its native link while preserving row semantics', async ({ page, accessibleName, expectedPath }) => {
    const user = userEvent.setup()
    renderPage(page)

    const link = await screen.findByRole('link', { name: accessibleName })
    expect(link).toHaveAttribute('href', expectedPath)

    const row = link.closest('tr')
    expect(row).toHaveRole('row')
    expect(row).not.toHaveAttribute('role')
    expect(row).not.toHaveAttribute('tabindex')

    link.focus()
    await user.keyboard('{Enter}')

    expect(screen.getByRole('status', { name: 'Current path' })).toHaveTextContent(expectedPath)
  })
})

describe('profile context', () => {
  it('saves professional context as structured profile fields', async () => {
    const { updateMe } = await import('../api')
    const user = userEvent.setup()
    renderPage(<ProfilePage />)

    await user.type(screen.getByLabelText('Professional role'), 'Attorney')
    await user.type(screen.getByLabelText('Job title'), 'Litigation Associate')
    await user.type(screen.getByLabelText('Office location'), 'Chicago, IL')
    await user.type(screen.getByLabelText('Primary jurisdictions'), 'Illinois, Wisconsin')
    await user.click(screen.getByRole('button', { name: 'Save context' }))

    await waitFor(() => expect(updateMe).toHaveBeenCalledWith({
      professional_role: 'Attorney',
      job_title: 'Litigation Associate',
      office_location: 'Chicago, IL',
      primary_jurisdictions: ['Illinois', 'Wisconsin'],
    }))
    expect(screen.getByText('Your profile context has been saved.')).toBeInTheDocument()
  })
})
