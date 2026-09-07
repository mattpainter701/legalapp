import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import DocumentFieldEditor from './DocumentFieldEditor'

afterEach(cleanup)
it('requires a name and a valid optional automation key, supports Enter and Escape', () => {
  const save = vi.fn()
  const cancel = vi.fn()
  render(<DocumentFieldEditor text="Ada" onSave={save} onCancel={cancel} />)
  fireEvent.click(screen.getByRole('button', { name: 'Create field' }))
  expect(screen.getByRole('alert')).toHaveTextContent('Give this field a name')
  fireEvent.change(screen.getByLabelText('Document field name'), { target: { value: 'Client' } })
  fireEvent.change(screen.getByLabelText('Document automation key'), { target: { value: '2 invalid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create field' }))
  expect(screen.getByRole('alert')).toHaveTextContent('starting with a letter')
  expect(save).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Document automation key'), { target: { value: 'client_name' } })
  fireEvent.keyDown(screen.getByLabelText('Document field name'), { key: 'Enter' })
  expect(save).toHaveBeenCalledWith({ name: 'client_name', label: 'Client', field_type: 'text' })
  fireEvent.keyDown(screen.getByLabelText('Document field name'), { key: 'Escape' })
  expect(cancel).toHaveBeenCalledTimes(1)
})

it('keeps Word choice types intact', () => {
  render(<DocumentFieldEditor field={{ name: 'yes', field_type: 'checkbox', docx_choice: {} }} onCancel={vi.fn()} />)
  expect(screen.getByLabelText('Document field type')).toBeDisabled()
})
