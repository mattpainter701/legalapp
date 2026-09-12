import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getFirmBranding, updateFirmBranding } from '../api'
import FirmProfilePanel from './FirmProfilePanel'

vi.mock('../api', () => ({ getFirmBranding: vi.fn(), updateFirmBranding: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

// A tenant that never set an override: the API resolves firm_name through the
// domain-derived tenant name, and firm_name_override stays null.
const derived = {
  firm_name: 'Painterlaw',
  firm_name_override: null,
  tenant_name: 'Painterlaw',
  tenant_domain: 'painterlaw.com',
  firm_currency: 'USD',
}

beforeEach(() => {
  getFirmBranding.mockResolvedValue(derived)
  updateFirmBranding.mockImplementation(async (body) => ({
    ...derived,
    ...body,
    firm_name_override: body.firm_name,
    firm_name: body.firm_name || body.tenant_name,
  }))
})

it('offers the domain-derived account name for editing without pre-filling the override', async () => {
  render(<FirmProfilePanel />)
  const account = await screen.findByLabelText('Account name')
  expect(account).toHaveValue('Painterlaw')
  // The resolved firm_name equals the tenant name, but no override is stored,
  // so the display-name box stays empty rather than freezing the fallback in.
  expect(screen.getByLabelText('Display name')).toHaveValue('')
  expect(screen.getByLabelText('Tenant domain')).toHaveValue('painterlaw.com')
})

it('renames the tenant itself so the derived name is gone everywhere', async () => {
  render(<FirmProfilePanel />)
  const account = await screen.findByLabelText('Account name')
  await userEvent.clear(account)
  await userEvent.type(account, 'Painter Law Group')
  await userEvent.click(screen.getByRole('button', { name: 'Save firm profile' }))

  await waitFor(() => expect(updateFirmBranding).toHaveBeenCalled())
  const body = updateFirmBranding.mock.calls[0][0]
  expect(body.tenant_name).toBe('Painter Law Group')
  expect(body.firm_name).toBeNull()
  expect(await screen.findByText('Firm profile saved.')).toBeInTheDocument()
})

it('previews the name clients will see, preferring the display name', async () => {
  render(<FirmProfilePanel />)
  await screen.findByLabelText('Display name')
  await userEvent.type(screen.getByLabelText('Display name'), 'Painter Law Group, PLLC')
  expect(screen.getByText('Painter Law Group, PLLC')).toBeInTheDocument()
})

it('refuses to save a blank account name', async () => {
  render(<FirmProfilePanel />)
  await userEvent.clear(await screen.findByLabelText('Account name'))
  await userEvent.click(screen.getByRole('button', { name: 'Save firm profile' }))
  expect(await screen.findByText('Account name cannot be blank.')).toBeInTheDocument()
  expect(updateFirmBranding).not.toHaveBeenCalled()
})

it('sends the currency the API expects and surfaces its rejection', async () => {
  render(<FirmProfilePanel />)
  await userEvent.clear(await screen.findByLabelText('Currency'))
  await userEvent.type(screen.getByLabelText('Currency'), 'GBP')
  await userEvent.click(screen.getByRole('button', { name: 'Save firm profile' }))
  await waitFor(() => expect(updateFirmBranding).toHaveBeenCalled())
  expect(updateFirmBranding.mock.calls[0][0].firm_currency).toBe('GBP')

  updateFirmBranding.mockRejectedValueOnce({
    response: { data: { detail: 'firm_currency must be a 3-letter ISO 4217 code' } },
  })
  await userEvent.click(screen.getByRole('button', { name: 'Save firm profile' }))
  expect(
    await screen.findByText('firm_currency must be a 3-letter ISO 4217 code')
  ).toBeInTheDocument()
})
