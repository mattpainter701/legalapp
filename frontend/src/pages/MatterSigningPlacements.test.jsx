import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { SignatureRequestsPanel } from './MatterDetailPage'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn().mockResolvedValue([]) : value]))
})
vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'user' } }) }))
vi.mock('../components/templates/GeneratedSigningPlacementReview', () => ({ default: ({ onChange }) => <button type="button" onClick={() => onChange([{ field_id: 'manual', role: 'client', source_sha256: 'verified-final' }])}>Confirm final PDF placement</button> }))

beforeEach(() => {
  vi.clearAllMocks()
  api.createSignatureRequest.mockResolvedValue({ id: 'request' })
  api.sendSignatureRequest.mockResolvedValue({ id: 'request', status: 'sent' })
})
afterEach(cleanup)

async function fillSigner() {
  fireEvent.change(screen.getByPlaceholderText('Signer 1 full name'), { target: { value: 'Client Name' } })
  fireEvent.change(screen.getByPlaceholderText('Signer email'), { target: { value: 'client@example.test' } })
}

it('sends the generated PDF descriptor through the selected provider', async () => {
  const fields = [{ field_id: 'client', role: 'client', source_sha256: 'final' }]
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'pdf', filename: 'Generated.pdf', positioned_fields: fields, signing_placement_required: true }] })
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Generated.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'pdf' } })
  expect(screen.getByLabelText('Signing provider')).toHaveValue('dropbox_sign')
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.sendSignatureRequest).toHaveBeenCalledWith('matter', 'request'))
  expect(api.createSignatureRequest).toHaveBeenCalledWith('matter', expect.objectContaining({ provider: 'dropbox_sign', document_id: 'pdf', positioned_fields: fields }))
})

it('requires final placement review for a reflowed Word document before sending', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'word-pdf', filename: 'Reflowed.pdf', signing_placement_required: true, positioned_fields: [] }] })
  api.getMatterDocumentSigningSource.mockResolvedValue(new Blob(['final pdf']))
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Reflowed.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'word-pdf' } })
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  expect(api.createSignatureRequest).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Review PDF signing positions' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm final PDF placement' }))
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.sendSignatureRequest).toHaveBeenCalledOnce())
  expect(api.getMatterDocumentSigningSource).toHaveBeenCalledWith('matter', 'word-pdf')
  expect(api.createSignatureRequest.mock.calls[0][1].positioned_fields[0].source_sha256).toBe('verified-final')
})


it('retains custom Word roles and rejects a partial final placement review', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'roles', filename: 'Roles.pdf', signing_placement_required: true, signing_roles: ['client', 'landlord'], positioned_fields: [] }] })
  api.getMatterDocumentSigningSource.mockResolvedValue(new Blob(['final pdf']))
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Roles.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'roles' } })
  expect(screen.getByRole('option', { name: 'landlord' })).toBeInTheDocument()
  await fillSigner()
  fireEvent.click(screen.getByRole('button', { name: 'Review PDF signing positions' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm final PDF placement' }))
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  expect(await screen.findByText('Add signing fields for every role required by this document.')).toBeInTheDocument()
  expect(api.createSignatureRequest).not.toHaveBeenCalled()
})
