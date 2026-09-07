import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import WordCleanupAction from './WordCleanupAction'
import { cleanupWordTemplateDraft } from '../../api'

vi.mock('../../api', () => ({ cleanupWordTemplateDraft: vi.fn() }))

it('sends the exact selected source span and keeps the action on a new draft', async () => {
  const onCreated = vi.fn()
  cleanupWordTemplateDraft.mockResolvedValue({ id: 'cleaned-draft' })
  render(<WordCleanupAction
    templateId="derived"
    selection={{ paragraph_ordinal: 4, start: 7, end: 19, original_text: 'Dear {{name}}' }}
    onCreated={onCreated}
  />)

  fireEvent.change(screen.getByLabelText('Replacement Word text'), { target: { value: 'Dear {{name}},' } })
  fireEvent.click(screen.getByText('Create cleaned draft'))
  await waitFor(() => expect(cleanupWordTemplateDraft).toHaveBeenCalledWith('derived', {
    paragraph_ordinal: 4,
    start: 7,
    end: 19,
    original_text: 'Dear {{name}}',
    replacement_text: 'Dear {{name}},',
  }))
  expect(onCreated).toHaveBeenCalledWith({ id: 'cleaned-draft' })
})
