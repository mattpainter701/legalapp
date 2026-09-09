import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MatterTemplatePicker from './MatterTemplatePicker'
const api = vi.hoisted(() => ({ getTemplates: vi.fn(), getTemplate: vi.fn() }))
vi.mock('../../api', () => api)
vi.mock('../../pages/TemplatesPage', () => ({ RenderModal: ({ fixedMatterId, folderId, onSaved }) => <button onClick={() => onSaved({ matter_document_id: 'saved' })}>Review {fixedMatterId} in {folderId}</button> }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
describe('matter template attachment', () => {
  it('loads the chosen full template and keeps the matter and folder fixed', async () => {
    api.getTemplates.mockResolvedValue({ items: [{ id: 't', title: 'Questionnaire', is_active: true }], total: 1 })
    api.getTemplate.mockResolvedValue({ id: 't', is_active: true })
    const saved = vi.fn()
    render(<MatterTemplatePicker matterId="jane" folderId="intake" onClose={vi.fn()} onSaved={saved} />)
    fireEvent.click(await screen.findByText('Select and preview'))
    fireEvent.click(await screen.findByText('Review jane in intake'))
    expect(api.getTemplate).toHaveBeenCalledWith('t')
    expect(saved).toHaveBeenCalledWith({ matter_document_id: 'saved' })
  })
  it('searches the library and presents load failures without an empty success state', async () => {
    api.getTemplates.mockRejectedValue(new Error('offline'))
    render(<MatterTemplatePicker matterId="jane" onClose={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load')
    api.getTemplates.mockResolvedValue({ items: [], total: 0 })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'divorce' } })
    await waitFor(() => expect(api.getTemplates).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'divorce', offset: 0 })))
    expect(await screen.findByText('No matching active templates.')).toBeVisible()
  })
})
