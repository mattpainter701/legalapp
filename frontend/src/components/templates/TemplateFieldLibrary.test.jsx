import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import TemplateFieldLibrary from './TemplateFieldLibrary'
import { getTemplateFieldLibrary, getTemplateFieldUsage } from '../../api'

vi.mock('../../api', () => ({ getTemplateFieldLibrary: vi.fn(), getTemplateFieldUsage: vi.fn() }))
const fields = [
  { path: 'client.name', label: 'Client name', group: 'Client', suggested_name: 'client_name', template_count: 23 },
  { path: 'matter.court', label: 'Court', group: 'Matter', suggested_name: 'court', template_count: 0 },
  { path: 'custom.matter.example', label: 'Marriage date', group: 'Matter details', field_type: 'date', template_count: 1, options: [] },
  { path: 'item.party_name', label: 'Party name', group: 'Repeating item', template_count: 0 },
]
const empty = { items: [], total: 0, offset: 0, has_more: false }
const setup = () => render(<MemoryRouter><TemplateFieldLibrary /></MemoryRouter>)

beforeEach(() => {
  vi.resetAllMocks()
  getTemplateFieldLibrary.mockResolvedValue({ fields })
  getTemplateFieldUsage.mockResolvedValue(empty)
})
afterEach(cleanup)

it('searches the shared catalog and explains explicit Word conventions', async () => {
  setup()
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Find a shared field'), 'marriage')
  expect(screen.getByRole('button', { name: /Marriage date/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Client name/ })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Data source'), 'Client')
  expect(screen.getByText('No shared fields match these filters.')).toBeInTheDocument()
  await user.click(screen.getByText('How to mark fields in Word'))
  expect(screen.getByText(/Bold, underline and blank lines are review hints/)).toBeVisible()
  expect(getTemplateFieldUsage).not.toHaveBeenCalled()
})

it('loads all usage pages independently of the template card page and opens Studio', async () => {
  const item = { template_id: '6fa912fc-9589-465b-b340-8d2887b7e603', title: 'Fee agreement', status: 'draft', current_version_no: 2, fields: [{ name: 'client', label: 'Client full name' }] }
  getTemplateFieldUsage.mockResolvedValueOnce({ items: [item], total: 23, offset: 0, has_more: true })
    .mockResolvedValueOnce({ items: [{ ...item, title: 'Settlement agreement' }], total: 23, offset: 20, has_more: false })
  setup()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: /Client name/ }))
  expect(await screen.findByRole('link', { name: 'Fee agreement' })).toHaveAttribute('href', `/templates/${item.template_id}/studio`)
  expect(screen.getByText('Mapped fields: Client full name')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByRole('link', { name: 'Settlement agreement' })).toBeInTheDocument()
  expect(getTemplateFieldUsage).toHaveBeenLastCalledWith({ binding: 'client.name', limit: 20, offset: 20 })
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Previous' }))
  await waitFor(() => expect(getTemplateFieldUsage).toHaveBeenLastCalledWith({ binding: 'client.name', limit: 20, offset: 0 }))
})

it('discards stale usage responses when switching fields and resets pagination', async () => {
  let resolveFirst
  getTemplateFieldUsage.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
  setup()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: /Client name/ }))
  await user.click(screen.getByRole('button', { name: /Court/ }))
  expect(await screen.findByText(/No templates explicitly use this source yet/)).toBeInTheDocument()
  resolveFirst({ items: [{ template_id: 'wrong', title: 'Stale result', fields: [] }], total: 1, offset: 0 })
  await waitFor(() => expect(screen.queryByText('Stale result')).not.toBeInTheDocument())
  expect(getTemplateFieldUsage).toHaveBeenLastCalledWith({ binding: 'matter.court', limit: 20, offset: 0 })
})

it('recovers catalog and usage failures and shows custom/repeating field guidance', async () => {
  getTemplateFieldLibrary.mockRejectedValueOnce(new Error('offline'))
  getTemplateFieldUsage.mockRejectedValueOnce({ response: { data: { detail: 'Try again later' } } })
  setup()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Retry field library' }))
  await user.click(await screen.findByRole('button', { name: /Marriage date/ }))
  expect(await screen.findByText('Try again later')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry template usage' }))
  expect(await screen.findByText(/No templates explicitly use this source yet/)).toBeInTheDocument()
  expect(screen.getByText(/Give the Word placeholder a descriptive name/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /Party name/ }))
  expect(screen.getByText(/belongs inside a repeating section/)).toBeInTheDocument()
})
