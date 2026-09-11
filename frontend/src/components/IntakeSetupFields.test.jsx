import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useState } from 'react'
import IntakeSetupFields, { defaultIntakeSetup, intakeOptions } from './IntakeSetupFields'
import StarterPaperworkCard from './templates/StarterPaperworkCard'
import { getIntakeStarterPack, installIntakeStarterDocuments } from '../api'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getIntakeStarterPack: vi.fn(),
  installIntakeStarterDocuments: vi.fn(),
}))
afterEach(cleanup)
beforeEach(() => vi.resetAllMocks())

const familyPack = {
  practice: 'family', practice_label: 'Family and domestic relations', matter_type: 'general', practice_area: 'Family Law',
  questions: [{ key: 'matter_summary', label: 'What happened?' }, { key: 'children', label: 'List each child involved.' }],
  upload_requirements: [{ key: 'upload_family_orders', label: 'Any existing court orders' }],
  documents: [{ key: 'fee_agreement', title: 'Standard Fee Agreement — Legal Representation' }],
}

function Harness(props) {
  const [value, setValue] = useState(defaultIntakeSetup)
  return <><IntakeSetupFields value={value} onChange={setValue} onFile={() => {}} {...props} />
    <pre data-testid="questions">{value.questions}</pre><pre data-testid="uploads">{value.upload_requirements}</pre></>
}

it('loads the questions for the matter type on request', async () => {
  const user = userEvent.setup()
  getIntakeStarterPack.mockResolvedValue(familyPack)
  render(<Harness matterType="general" practiceArea="Family Law" />)

  await user.click(screen.getByRole('button', { name: /standard questions/i }))

  await waitFor(() => expect(screen.getByTestId('questions')).toHaveTextContent('List each child involved.'))
  expect(getIntakeStarterPack).toHaveBeenCalledWith({ matter_type: 'general', practice_area: 'Family Law' })
  expect(screen.getByTestId('uploads')).toHaveTextContent('Any existing court orders')
  expect(screen.getByRole('status')).toHaveTextContent('Family and domestic relations')
})

it('reads the matter type from the matter when there is one', async () => {
  const user = userEvent.setup()
  getIntakeStarterPack.mockResolvedValue(familyPack)
  render(<Harness matterId="matter-1" />)

  await user.click(screen.getByRole('button', { name: /standard questions/i }))

  await waitFor(() => expect(getIntakeStarterPack).toHaveBeenCalledWith({ matter_id: 'matter-1' }))
})

it('keeps the typed questions when the standard pack cannot be loaded', async () => {
  const user = userEvent.setup()
  getIntakeStarterPack.mockRejectedValue(new Error('offline'))
  render(<Harness matterType="Divorce" />)

  await user.click(screen.getByRole('button', { name: /standard questions/i }))

  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Could not load'))
  expect(screen.getByTestId('questions')).toHaveTextContent('Please describe your legal matter.')
})

it('sends loaded questions and uploads as required intake requirements', () => {
  const options = intakeOptions({ ...defaultIntakeSetup, questions: 'What happened?\nList each child involved.', upload_requirements: 'Any existing court orders' }, 'client@example.com')

  expect(options.questions).toEqual([
    { key: 'question_1', label: 'What happened?', required: true },
    { key: 'question_2', label: 'List each child involved.', required: true },
  ])
  // An undated requirement carries due_at: null rather than omitting it, so the
  // server sees the same shape whether or not the firm set a deadline.
  expect(options.upload_requirements).toEqual([{ key: 'upload_1', label: 'Any existing court orders', required: true, due_at: null }])
})

it('installs the standard paperwork as drafts and reports what changed', async () => {
  const user = userEvent.setup(); const onInstalled = vi.fn()
  installIntakeStarterDocuments.mockResolvedValue({ documents: [
    { key: 'fee_agreement', title: 'Standard Fee Agreement — Legal Representation', created: true },
    { key: 'client_intake_form', title: 'Client Intake Form', created: false },
  ] })
  render(<StarterPaperworkCard onInstalled={onInstalled} />)

  await user.click(screen.getByRole('button', { name: /add the standard client paperwork/i }))

  await waitFor(() => expect(screen.getByText(/Standard Fee Agreement.*added as a draft for attorney review/)).toBeInTheDocument())
  expect(screen.getByText(/Client Intake Form — already in your library/)).toBeInTheDocument()
  expect(onInstalled).toHaveBeenCalled()
})

it('reports a failed install instead of implying the templates exist', async () => {
  const user = userEvent.setup()
  installIntakeStarterDocuments.mockRejectedValue(new Error('denied'))
  render(<StarterPaperworkCard />)

  await user.click(screen.getByRole('button', { name: /add the standard client paperwork/i }))

  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not add the standard client paperwork'))
})

it('carries deadlines set at matter creation into the intake request', () => {
  const setup = {
    ...defaultIntakeSetup,
    timezone: 'UTC',
    agreement_due: '2026-10-01',
    questionnaire_due: '2026-10-05',
    uploads_due: '2026-10-09',
    upload_requirements: 'Marriage certificate',
    selected_documents: [{ document_id: 'form', label: 'Retainer addendum', requires_signature: true, due: '2026-10-03' }],
  }
  const options = intakeOptions(setup, 'jane@example.com')
  expect(options.agreement_due_at).toBe('2026-10-01T17:00:00.000Z')
  expect(options.questionnaire_due_at).toBe('2026-10-05T17:00:00.000Z')
  expect(options.selected_documents[0].due_at).toBe('2026-10-03T17:00:00.000Z')
  expect(options.upload_requirements[0].due_at).toBe('2026-10-09T17:00:00.000Z')
})

it('drops the questionnaire deadline when no questionnaire is sent', () => {
  const options = intakeOptions({
    ...defaultIntakeSetup, timezone: 'UTC', include_questionnaire: false, questionnaire_due: '2026-10-05',
  }, 'jane@example.com')
  expect(options.questionnaire_due_at).toBeNull()
})
