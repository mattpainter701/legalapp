import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import WordDeriveDraftAction from './WordDeriveDraftAction'
import { deriveWordTemplateDraft } from '../../api'

vi.mock('../../api', () => ({ deriveWordTemplateDraft: vi.fn(), getTemplateOriginalSource: vi.fn() }))

it('creates a new draft only after the explicit action', async () => {
  const onCreated = vi.fn(); deriveWordTemplateDraft.mockResolvedValue({ id: 'draft' })
  render(<WordDeriveDraftAction templateId="master" fields={[{ name: 'amount' }]} onCreated={onCreated} />)
  expect(deriveWordTemplateDraft).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Word source type'), { target: { value: 'form' } })
  fireEvent.click(screen.getByText('Create derived draft'))
  await waitFor(() => expect(deriveWordTemplateDraft).toHaveBeenCalledWith('master', expect.objectContaining({ source_mode: 'form' })))
  expect(onCreated).toHaveBeenCalledWith({ id: 'draft' })
})
