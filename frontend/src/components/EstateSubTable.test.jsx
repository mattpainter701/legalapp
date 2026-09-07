import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import EstateSubTable from './EstateSubTable'

const api = vi.hoisted(() => ({
  listEstateChildren: vi.fn(), createEstateChild: vi.fn(), updateEstateChild: vi.fn(), deleteEstateChild: vi.fn(),
}))
vi.mock('../api', () => api)
vi.mock('./dialog/ConfirmProvider', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }))

const fields = [{ key: 'name', label: 'Name', type: 'text', required: true }, { key: 'count', label: 'Count', type: 'number' }]
const columns = [{ key: 'name', label: 'Name' }, { key: 'count', label: 'Count' }]
const renderTable = () => render(<EstateSubTable estateId="estate-1" resource="assets" title="Assets" columns={columns} fields={fields} />)

beforeEach(() => {
  vi.clearAllMocks()
  api.listEstateChildren.mockResolvedValue([{ id: 'row-1', name: 'Zero asset', count: 0 }])
  api.createEstateChild.mockResolvedValue({ id: 'row-2', name: 'New' })
  api.updateEstateChild.mockResolvedValue({ id: 'row-1', name: 'Updated', count: 0 })
})
afterEach(() => cleanup())

it('renders known zero values instead of treating them as missing', async () => {
  renderTable()
  expect(await screen.findByText('0')).toBeInTheDocument()
})

it('shows load failures in the empty state', async () => {
  api.listEstateChildren.mockRejectedValueOnce(new Error('offline'))
  renderTable()
  expect(await screen.findByRole('alert')).toHaveTextContent('Failed to load.')
  expect(screen.queryByText('No entries yet.')).not.toBeInTheDocument()
})

it('retries a failed list load', async () => {
  api.listEstateChildren.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([{ id: 'row-2', name: 'Recovered', count: 0 }])
  renderTable()
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Recovered')).toBeInTheDocument()
})

it('validates required create fields inline and clears the error after retry', async () => {
  renderTable()
  await screen.findByText('Zero asset')
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Name is required.')
  fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Added asset' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(api.createEstateChild).toHaveBeenCalled())
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('preserves an edited draft after a failed save and recovers on retry', async () => {
  api.updateEstateChild.mockRejectedValueOnce(new Error('conflict'))
  renderTable()
  await screen.findByText('Zero asset')
  fireEvent.click(screen.getByRole('button', { name: 'Edit Assets entry' }))
  const name = screen.getByLabelText(/Name/)
  fireEvent.change(name, { target: { value: 'Retried asset' } })
  fireEvent.click(screen.getByRole('button', { name: 'Update' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Failed to save.')
  expect(name).toHaveValue('Retried asset')
  fireEvent.click(screen.getByRole('button', { name: 'Update' }))
  await waitFor(() => expect(api.updateEstateChild).toHaveBeenCalledTimes(2))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('validates required fields on update', async () => {
  renderTable()
  await screen.findByText('Zero asset')
  fireEvent.click(screen.getByRole('button', { name: 'Edit Assets entry' }))
  fireEvent.change(screen.getByLabelText(/Name/), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Update' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Name is required.')
  expect(api.updateEstateChild).not.toHaveBeenCalled()
})

it('shows delete failures while retaining existing rows', async () => {
  api.deleteEstateChild.mockRejectedValueOnce(new Error('offline'))
  renderTable()
  await screen.findByText('Zero asset')
  fireEvent.click(screen.getByRole('button', { name: 'Delete Assets entry' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Failed to delete.')
  expect(screen.getByText('Zero asset')).toBeInTheDocument()
})

it('locks the draft and cancel while a save is pending', async () => {
  let resolveUpdate
  api.updateEstateChild.mockReturnValueOnce(new Promise((resolve) => { resolveUpdate = resolve }))
  renderTable()
  await screen.findByText('Zero asset')
  fireEvent.click(screen.getByRole('button', { name: 'Edit Assets entry' }))
  const name = screen.getByLabelText(/Name/)
  fireEvent.click(screen.getByRole('button', { name: 'Update' }))
  expect(name).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  resolveUpdate({ id: 'row-1', name: 'Updated' })
})

it('ignores a stale list response after the resource changes', async () => {
  let resolveOld
  api.listEstateChildren.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve })).mockResolvedValueOnce([{ id: 'row-2', name: 'New resource', count: 1 }])
  const { rerender } = render(<EstateSubTable estateId="estate-1" resource="assets" title="Assets" columns={columns} fields={fields} />)
  rerender(<EstateSubTable estateId="estate-2" resource="liabilities" title="Liabilities" columns={columns} fields={fields} />)
  expect(await screen.findByText('New resource')).toBeInTheDocument()
  resolveOld([{ id: 'old', name: 'Old resource', count: 2 }])
  await Promise.resolve()
  expect(screen.queryByText('Old resource')).not.toBeInTheDocument()
})
