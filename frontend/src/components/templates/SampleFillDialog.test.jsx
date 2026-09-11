import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderSampleTemplateFile } from '../../api'
import SampleFillDialog from './SampleFillDialog'

vi.mock('../../api', () => ({ renderSampleTemplateFile: vi.fn() }))

const sample = {
  id: 'will-1',
  title: 'Last Will and Testament',
  variable_schema: {
    version: 1,
    fields: [
      { name: 'client_name', label: 'Client Name', field_type: 'text', required: true },
      { name: 'waiver', label: 'Include Waiver', field_type: 'checkbox' },
      { name: 'county', label: 'County', field_type: 'choice', options: [{ value: 'wake', label: 'Wake' }, { value: 'durham', label: 'Durham' }] },
    ],
  },
}

describe('SampleFillDialog', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders text, checkbox, and choice inputs for the schema fields', () => {
    render(<SampleFillDialog sample={sample} onClose={vi.fn()} />)
    expect(screen.getByLabelText('Client Name *')).toBeInTheDocument()
    expect(screen.getByLabelText('Include Waiver')).toBeInTheDocument()
    expect(screen.getByLabelText('County')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Wake' })).toBeInTheDocument()
  })

  it('submits entered values and downloads the filled PDF', async () => {
    const createUrl = vi.fn(() => 'blob:filled')
    const revokeUrl = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL: createUrl, revokeObjectURL: revokeUrl })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderSampleTemplateFile.mockResolvedValue({ blob: new Blob(['%PDF']), filename: 'filled.pdf' })
    const onClose = vi.fn()
    render(<SampleFillDialog sample={sample} onClose={onClose} />)
    fireEvent.change(screen.getByLabelText('Client Name *'), { target: { value: 'Ada Lovelace' } })
    fireEvent.click(screen.getByLabelText('Include Waiver'))
    fireEvent.click(screen.getByRole('button', { name: 'Download filled PDF' }))
    await waitFor(() => expect(renderSampleTemplateFile).toHaveBeenCalledWith('will-1', {
      variables: { client_name: 'Ada Lovelace', waiver: 'Yes' },
    }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(createUrl).toHaveBeenCalled()
    expect(revokeUrl).toHaveBeenCalledWith('blob:filled')
    clickSpy.mockRestore()
  })

  it('keeps the dialog open with an alert when filling fails', async () => {
    renderSampleTemplateFile.mockRejectedValue(new Error('The sample could not be filled.'))
    const onClose = vi.fn()
    render(<SampleFillDialog sample={sample} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download filled PDF' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be filled')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes without submitting when cancelled', () => {
    const onClose = vi.fn()
    render(<SampleFillDialog sample={sample} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
    expect(renderSampleTemplateFile).not.toHaveBeenCalled()
  })
})
