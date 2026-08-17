import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PluginPage from './PluginPage'
import { executeSkill, extractSkillInput, getPlugins } from '../api'

const authHarness = { user: {} }

vi.mock('../App', () => ({
  useAuth: () => authHarness,
}))

vi.mock('../api', () => ({
  getPlugins: vi.fn(),
  extractSkillInput: vi.fn(),
  getPluginProfile: vi.fn().mockResolvedValue({
    profile_content: 'Firm profile',
    is_complete: true,
  }),
  getPluginSetup: vi.fn().mockResolvedValue({ setup: {}, health: {} }),
  savePluginSetup: vi.fn(),
  executeSkill: vi.fn(),
  getMattersV2: vi.fn().mockResolvedValue({ items: [] }),
}))

function renderPluginPage(pluginName = 'commercial-legal') {
  return render(
    <MemoryRouter initialEntries={[`/plugins/${pluginName}`]}>
      <Routes>
        <Route path="/plugins/:pluginName" element={<PluginPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('PluginPage skill execution', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authHarness.user = {}
    getPlugins.mockResolvedValue([
      {
        plugin_name: 'commercial-legal',
        display_name: 'Commercial Legal',
        skills: ['nda-review', 'cold-start-interview'],
        primary_route: '/plugins/commercial/renewals',
      },
    ])
  })
  afterEach(cleanup)

  // Regression: PluginPage rendered <Bot /> in the results header without
  // importing it from lucide-react. The output block only mounts once a run
  // returns, so every successful skill run threw a ReferenceError into the
  // error boundary *after* the tokens had already been billed. This test
  // exercises that exact path.
  it('renders the analysis result after a successful run', async () => {
    executeSkill.mockResolvedValue({
      memo: 'Indemnity is uncapped and should be negotiated.',
      requires_attorney_review: true,
      gates_triggered: [],
      model_used: 'deepseek-chat',
      tokens_used: 1234,
    })
    const user = userEvent.setup()
    renderPluginPage()

    const input = await screen.findByPlaceholderText(/paste nda review content here/i)
    await user.type(input, 'Mutual NDA between Acme and Beta.')
    await user.click(screen.getByRole('button', { name: /run nda review/i }))

    expect(await screen.findByText('Analysis Results')).toBeInTheDocument()
    expect(
      screen.getByText('Indemnity is uncapped and should be negotiated.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /discuss in chat/i })).toBeInTheDocument()
  })

  it('shows a readable failure instead of a blank panel when the run errors', async () => {
    executeSkill.mockRejectedValue({
      response: { data: { detail: 'Add-on not enabled for this firm.' } },
    })
    const user = userEvent.setup()
    renderPluginPage()

    const input = await screen.findByPlaceholderText(/paste nda review content here/i)
    await user.type(input, 'Mutual NDA.')
    await user.click(screen.getByRole('button', { name: /run nda review/i }))

    expect(await screen.findByText('Analysis Failed')).toBeInTheDocument()
    expect(screen.getByText('Add-on not enabled for this firm.')).toBeInTheDocument()
  })

  // Regression: the picker accepted .pdf/.docx but read them with
  // FileReader.readAsText, pushing raw binary into the model input.
  // Extraction is now server-side.
  it('extracts uploaded document text server-side instead of reading bytes', async () => {
    extractSkillInput.mockResolvedValue({
      filename: 'nda.pdf',
      text: 'MUTUAL NON-DISCLOSURE AGREEMENT',
      characters: 31,
      truncated: false,
      ocr_used: false,
    })
    const user = userEvent.setup()
    renderPluginPage()

    const fileInput = await screen.findByLabelText(/input materials/i)
    await user.upload(
      fileInput,
      new File(['%PDF-1.7 binary'], 'nda.pdf', { type: 'application/pdf' })
    )

    await waitFor(() => expect(extractSkillInput).toHaveBeenCalledTimes(1))
    expect(extractSkillInput.mock.calls[0][0]).toBeInstanceOf(File)
    expect(
      await screen.findByDisplayValue('MUTUAL NON-DISCLOSURE AGREEMENT')
    ).toBeInTheDocument()
    expect(screen.getByText(/31 characters extracted/i)).toBeInTheDocument()
  })

  it('warns that OCR text needs checking against the original', async () => {
    extractSkillInput.mockResolvedValue({
      filename: 'scanned.pdf',
      text: 'SCANNED ORDER',
      characters: 13,
      truncated: false,
      ocr_used: true,
      pages_analyzed: 3,
    })
    const user = userEvent.setup()
    renderPluginPage()

    const fileInput = await screen.findByLabelText(/input materials/i)
    await user.upload(
      fileInput,
      new File(['scan'], 'scanned.pdf', { type: 'application/pdf' })
    )

    expect(
      await screen.findByText(/text recovered by OCR from 3 page\(s\)/i)
    ).toBeInTheDocument()
  })

  it('reports an unreadable file instead of loading binary into the input', async () => {
    extractSkillInput.mockRejectedValue({
      response: { data: { detail: 'This file could not be read.' } },
    })
    const user = userEvent.setup()
    renderPluginPage()

    const fileInput = await screen.findByLabelText(/input materials/i)
    await user.upload(
      fileInput,
      new File(['junk'], 'broken.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })
    )

    expect(await screen.findByText('This file could not be read.')).toBeInTheDocument()
  })

  it('hides the internal cold-start-interview skill from the workflow list', async () => {
    renderPluginPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Nda Review' })).toBeInTheDocument()
    )
    expect(
      screen.queryByRole('button', { name: /cold start interview/i })
    ).not.toBeInTheDocument()
  })

  it('hides Premium controls in a demo workspace', async () => {
    authHarness.user = { demo: { session_id: 'demo-session' } }
    renderPluginPage()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Nda Review' })).toBeInTheDocument()
    )
    expect(screen.queryByText('Premium Model')).not.toBeInTheDocument()
  })
})
