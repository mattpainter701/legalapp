import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClientDetailPage from './ClientDetailPage'
import {
  getClient, getClientContacts, getClientMatters, getContactCommunications, getTasks, syncClientQuickBooks,
} from '../api'

const authHarness = vi.hoisted(() => ({
  user: { id: 'user-1', role: 'admin', demo: { session_id: 'demo-1' } },
}))

vi.mock('../App', () => ({
  useAuth: () => authHarness,
}))

vi.mock('../api', () => ({
  getClient: vi.fn(),
  getClientContacts: vi.fn(),
  getClientMatters: vi.fn(),
  getContactCommunications: vi.fn(),
  getTasks: vi.fn(),
  syncClientQuickBooks: vi.fn(),
  updateClient: vi.fn(),
}))

const clientRecord = {
  id: 'client-1',
  display_name: 'Northstar Analytics, Inc.',
  entity_type: 'organization',
  client_status: 'active',
  client_number: 'DEMO-CL-0001',
  organization_name: 'Northstar Analytics, Inc.',
  client_since: '2019-01-15',
  email: 'avery@example.invalid',
  phone: '(312) 555-0100',
  address: { city: 'Chicago', state: 'IL' },
  preferred_contact_method: 'phone',
  preferred_contact_window: 'Weekdays, 9:00 a.m.-noon',
  preferred_contact_timezone: 'America/Chicago',
  sms_opt_in: true,
  email_opt_in: true,
  billing_delivery_method: 'email',
  payment_terms_days: 30,
}

describe('ClientDetailPage demo account view', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authHarness.user = { id: 'user-1', role: 'admin', demo: { session_id: 'demo-1' } }
    getClient.mockResolvedValue(clientRecord)
    getClientMatters.mockResolvedValue([{ id: 'matter-1', matter_name: 'MSA review', status: 'open' }])
    getClientContacts.mockResolvedValue([{
      id: 'contact-1', display_name: 'Avery Nguyen', entity_type: 'person',
      email: 'avery@example.invalid', client_contact_role: 'Chief Operating Officer',
      is_primary_client_contact: true, client_contact_authorization: 'Authorized for routine instructions.',
    }])
    getContactCommunications.mockImplementation(contactId => Promise.resolve({
      items: contactId === 'contact-1' ? [{
        id: 'communication-1', subject: 'Client update', summary: 'Records supplied',
        occurred_at: '2026-08-20T15:00:00Z', channel: 'email', direction: 'inbound',
      }] : [],
    }))
    getTasks.mockResolvedValue({ items: [] })
  })

  afterEach(() => cleanup())

  it('groups related people and activity while disabling live demo sync', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/clients/client-1']}>
        <Routes><Route path="/clients/:id" element={<ClientDetailPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Northstar Analytics, Inc.' })).toBeInTheDocument()
    expect(screen.getByText('Avery Nguyen')).toBeInTheDocument()
    expect(screen.getByText('Chief Operating Officer · avery@example.invalid')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Activity' }))
    expect(await screen.findByText('Client update')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Billing & integrations' }))
    expect(screen.queryByRole('button', { name: /Sync to QuickBooks/i })).not.toBeInTheDocument()
    expect(screen.getByText('Live accounting synchronization is disabled in demo workspaces.')).toBeInTheDocument()
  })

  it('uses explicit simulation wording even when detail is omitted', async () => {
    authHarness.user = { id: 'user-1', role: 'admin', demo: null }
    syncClientQuickBooks.mockResolvedValue({
      status: 'demo_simulated',
      is_simulated: true,
      qbo_customer_id: 'DEMO-CLIENT1',
      detail: null,
    })
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/clients/client-1']}>
        <Routes><Route path="/clients/:id" element={<ClientDetailPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Northstar Analytics, Inc.' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Billing & integrations' }))
    await user.click(screen.getByRole('button', { name: /Sync to QuickBooks/i }))
    expect(
      await screen.findByText(
        'Demo simulation DEMO-CLIENT1; QuickBooks was not contacted.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(/is synchronized/i)).not.toBeInTheDocument()
  })
})
