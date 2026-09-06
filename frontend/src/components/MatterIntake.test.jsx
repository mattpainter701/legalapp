import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ClientIntakeChecklist from './ClientIntakeChecklist'
import MatterIntakePanel from './MatterIntakePanel'
import NewMatterModal from './NewMatterModal'
import api, { getClientIntake, submitClientIntake, createMatterV2, getContacts, getAdminUsers, getPlugins } from '../api'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getClientIntake: vi.fn(), submitClientIntake: vi.fn(), createMatterV2: vi.fn(), getContacts: vi.fn(), getAdminUsers: vi.fn(), getPlugins: vi.fn(), createContact: vi.fn(),
}))
afterEach(cleanup)
const packet = () => ({
  id: 'packet', matter_id: 'matter', status: 'awaiting_documents', requirements: { fee_agreement: { completed: false }, questionnaire: { completed: false } },
  questions: [{ key: 'summary', label: 'Describe your matter', required: true }], answers: {}, delivery: {}, sent_at: null, completed_at: null, meeting: null,
})
beforeEach(() => {
  vi.resetAllMocks()
  getClientIntake.mockResolvedValue(packet())
  api.get.mockResolvedValue({ data: packet() })
  getContacts.mockResolvedValue([{ id: 'client', first_name: 'Jane', last_name: 'Smith', email: 'jane@example.com' }])
  getAdminUsers.mockResolvedValue([])
  getPlugins.mockResolvedValue([])
})
it('keeps signature outstanding after questionnaire completion', async () => {
  const user = userEvent.setup(); const onSign = vi.fn()
  submitClientIntake.mockResolvedValue({ ...packet(), requirements: { fee_agreement: { completed: false }, questionnaire: { completed: true } } })
  render(<ClientIntakeChecklist onSign={onSign} />)
  await user.type(await screen.findByLabelText('Describe your matter *'), 'Case summary')
  await user.click(screen.getByRole('button', { name: 'Submit completed questionnaire' }))
  await screen.findByText('Questionnaire: Complete')
  expect(submitClientIntake).toHaveBeenCalledWith({ summary: 'Case summary' })
  expect(screen.queryByText(/paperwork is complete/)).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Review and sign fee agreement' }))
  expect(onSign).toHaveBeenCalledOnce()
})
it('preserves answers when submission fails', async () => {
  const user = userEvent.setup()
  submitClientIntake.mockRejectedValue({ response: { data: { detail: 'Storage unavailable; please retry' } } })
  render(<ClientIntakeChecklist />)
  await user.type(await screen.findByLabelText('Describe your matter *'), 'My answer')
  await user.click(screen.getByRole('button', { name: 'Submit completed questionnaire' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Storage unavailable')
  expect(screen.getByLabelText('Describe your matter *')).toHaveValue('My answer')
})
it('requires explicit reviewed retry for uncertain delivery', async () => {
  const user = userEvent.setup()
  api.get.mockResolvedValue({ data: { ...packet(), delivery: { 'welcome:email': { state: 'unknown', attempt: 0 } } } })
  api.post.mockResolvedValue({ data: packet() })
  render(<MatterIntakePanel matterId="matter" />)
  await user.click(await screen.findByRole('button', { name: 'Review delivery' }))
  expect(api.post).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: /I verified it was not sent/ }))
  expect(api.post).toHaveBeenCalledWith('/matters/matter/intake/retry', { delivery_key: 'welcome:email', confirm_not_sent: true })
})
it('records the selected external document and verification note', async () => {
  const user = userEvent.setup()
  api.post.mockResolvedValue({ data: packet() })
  render(<MatterIntakePanel matterId="matter" documents={[{ id: 'doc', filename: 'Executed agreement.pdf' }]} />)
  await user.click(await screen.findByText('Record a document received outside the portal'))
  await user.selectOptions(screen.getByLabelText('Received document'), 'doc')
  await user.type(screen.getByLabelText('Verification note'), 'Reviewed signature')
  await user.click(screen.getByRole('button', { name: 'Confirm document is complete' }))
  expect(api.post).toHaveBeenCalledWith('/matters/matter/intake/receipt', { requirement: 'fee_agreement', document_id: 'doc', note: 'Reviewed signature' })
})
it('offers call or in-person booking after both requirements complete', async () => {
  const user = userEvent.setup()
  api.get.mockResolvedValue({ data: { ...packet(), status: 'documents_complete', completed_at: '2026-09-06T14:00:00Z', requirements: { fee_agreement: { completed: true }, questionnaire: { completed: true } } } })
  api.post.mockResolvedValue({ data: packet() })
  render(<MatterIntakePanel matterId="matter" />)
  await user.selectOptions(await screen.findByLabelText('Meeting type'), 'in_person')
  fireEvent.change(screen.getByLabelText('Meeting date and time'), { target: { value: '2026-09-08T10:00' } })
  await user.type(screen.getByLabelText('Call details or office location'), 'Main office')
  await user.click(screen.getByRole('button', { name: 'Save meeting & notify client' }))
  expect(api.post).toHaveBeenCalledWith('/matters/matter/intake/meeting', expect.objectContaining({ kind: 'in_person', details: 'Main office', starts_at: expect.stringMatching(/Z$/) }))
})
it('retries intake without creating a duplicate matter after setup failure', async () => {
  const user = userEvent.setup(); const onCreated = vi.fn()
  createMatterV2.mockResolvedValue({ id: 'matter', matter_name: 'Smith case' })
  api.post.mockRejectedValueOnce({ response: { data: { detail: 'Storage unavailable' } } }).mockResolvedValueOnce({ data: packet() })
  render(<NewMatterModal open onClose={vi.fn()} onCreated={onCreated} />)
  await user.type(screen.getByLabelText(/Matter Title/), 'Smith case')
  await waitFor(() => expect(getContacts).toHaveBeenCalled())
  await user.selectOptions(screen.getByLabelText(/^Client$/), 'client')
  await user.click(screen.getByLabelText('Start client intake with this matter'))
  await user.upload(screen.getByLabelText('Reviewed fee agreement PDF'), new File(['%PDF-reviewed'], 'fee.pdf', { type: 'application/pdf' }))
  await user.click(screen.getByRole('button', { name: 'Create Matter & Start Intake' }))
  await screen.findByRole('button', { name: 'Retry intake packet' })
  expect(onCreated).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Retry intake packet' }))
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
  expect(createMatterV2).toHaveBeenCalledOnce()
  expect(screen.getByLabelText('Start client intake with this matter')).not.toBeChecked()
})

it('clears the previous intake when switching to a matter without a packet', async () => {
  const { rerender } = render(<MatterIntakePanel matterId="first" />)
  await screen.findByText('Initial packet sent: Not yet')
  api.get.mockRejectedValue({ response: { status: 404 } })
  rerender(<MatterIntakePanel matterId="second" />)
  await screen.findByRole('button', { name: 'Start intake & send portal invitation' })
  expect(screen.queryByText('Initial packet sent: Not yet')).not.toBeInTheDocument()
})
