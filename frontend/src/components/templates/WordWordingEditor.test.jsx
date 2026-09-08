import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanupWordTemplateDraft } from '../../api'
import WordWordingEditor, { wordTextChange } from './WordWordingEditor'

vi.mock('../../api', () => ({ cleanupWordTemplateDraft: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const selection = { ordinal: 2, start: 0, text: 'Dear Alex, welcome.' }
const template = { id: 'one', source_sha256: 'a'.repeat(64), current_version_no: 3 }

it('finds a precise Unicode edit and supports insertion, deletion and no change', () => {
  expect(wordTextChange(selection, 'Hello Alex, welcome.')).toEqual({ paragraph_ordinal: 2, start: 0, end: 4, original_text: 'Dear', replacement_text: 'Hello' })
  expect(wordTextChange(selection, selection.text)).toBeNull()
  expect(wordTextChange({ ordinal: 0, start: 5, text: '😀 X' }, '😀 Y')).toMatchObject({ start: 7, end: 8, original_text: 'X', replacement_text: 'Y' })
  expect(wordTextChange({ ordinal: 0, start: 0, text: 'AB' }, 'XAB')).toMatchObject({ start: 0, end: 1, original_text: 'A', replacement_text: 'XA' })
  expect(wordTextChange({ ordinal: 0, start: 0, text: 'AB' }, 'ABX')).toMatchObject({ start: 1, end: 2, original_text: 'B', replacement_text: 'BX' })
  expect(wordTextChange(selection, '')).toMatchObject({ original_text: selection.text, replacement_text: '' })
})

it('saves only the changed wording with source/version guards', async () => {
  const created = { id: 'revised' }
  cleanupWordTemplateDraft.mockResolvedValue(created)
  const onCreated = vi.fn()
  render(<WordWordingEditor template={template} selection={selection} onCreated={onCreated} onCancel={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Save revised draft' })).toBeDisabled()
  const user = userEvent.setup()
  await user.clear(screen.getByLabelText('Document wording'))
  await user.type(screen.getByLabelText('Document wording'), 'Hello Alex, welcome.')
  await user.click(screen.getByRole('button', { name: 'Save revised draft' }))
  expect(cleanupWordTemplateDraft).toHaveBeenCalledWith('one', { paragraph_ordinal: 2, start: 0, end: 4, original_text: 'Dear', replacement_text: 'Hello', expected_source_sha256: 'a'.repeat(64), expected_version_no: 3 })
  expect(onCreated).toHaveBeenCalledWith(created)
})

it('keeps an already-created draft when navigation fails and retries opening it', async () => {
  cleanupWordTemplateDraft.mockResolvedValue({ id: 'revised' })
  const onCreated = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined)
  render(<WordWordingEditor template={template} selection={selection} onCreated={onCreated} onCancel={vi.fn()} />)
  await userEvent.type(screen.getByLabelText('Document wording'), '!')
  await userEvent.click(screen.getByRole('button', { name: 'Save revised draft' }))
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Open revised draft' }))
  expect(cleanupWordTemplateDraft).toHaveBeenCalledTimes(1)
  expect(onCreated).toHaveBeenCalledTimes(2)
})
