import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import PortalCasePage from './PortalCasePage'
import { getPortalCase, logoutMediationPortal, uploadPortalDocument } from '../api'

vi.mock('../api', () => ({
  getPortalCase: vi.fn(),
  createPortalAsset: vi.fn(),
  updatePortalAsset: vi.fn(),
  submitPortalAsset: vi.fn(),
  decidePortalAsset: vi.fn(),
  uploadPortalDocument: vi.fn(),
  downloadPortalDocumentUrl: (id) => `/api/portal/mediation/documents/${id}/download`,
  createPortalProposal: vi.fn(),
  logoutMediationPortal: vi.fn(),
}))

vi.mock('../components/dialog/ConfirmProvider', () => ({
  useConfirm: () => () => Promise.resolve(true),
}))

vi.mock('../components/toast/useToast', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn() }),
}))

const caseView = {
  case: { id: 'case-1', case_name: 'Rivera mediation', party_a: 'Rivera', party_b: 'Northline', status: 'active' },
  party_role: 'party_a',
  party_id: 'party-1',
  my_assets: [],
  shared_assets: [],
  documents: [],
  proposals: [],
}

const renderPage = () => render(<MemoryRouter><PortalCasePage /></MemoryRouter>)

beforeEach(() => {
  vi.resetAllMocks()
  getPortalCase.mockResolvedValue(caseView)
  uploadPortalDocument.mockResolvedValue({})
  logoutMediationPortal.mockResolvedValue(undefined)
})

afterEach(cleanup)

it('enables upload once a file is chosen and shows the chosen name', async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole('heading', { name: 'Rivera mediation' })
  await user.click(screen.getByRole('button', { name: /Documents/ }))

  const uploadButton = screen.getByRole('button', { name: 'Upload' })
  // The bug this guards: with no onChange handler the button never left disabled.
  expect(uploadButton).toBeDisabled()

  const file = new File(['letter'], 'letter.pdf', { type: 'application/pdf' })
  fireEvent.change(screen.getByLabelText('Choose a document to upload'), { target: { files: [file] } })

  expect(screen.getByText('letter.pdf')).toBeInTheDocument()
  await waitFor(() => expect(uploadButton).toBeEnabled())

  await user.click(uploadButton)
  await waitFor(() => expect(uploadPortalDocument).toHaveBeenCalledWith(file, undefined, 'case-1'))
})

it('offers working escape routes when the case will not load', async () => {
  getPortalCase.mockRejectedValue({ response: { status: 401 } })
  renderPage()
  expect(await screen.findByText(/couldn't open your case/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Enter a different invitation code' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Go to firm login' })).toBeInTheDocument()
})

it('signs out after confirmation', async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole('heading', { name: 'Rivera mediation' })
  await user.click(screen.getByRole('button', { name: /Sign out/ }))
  await waitFor(() => expect(logoutMediationPortal).toHaveBeenCalledTimes(1))
})
