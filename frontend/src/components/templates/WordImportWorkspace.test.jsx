import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import WordImportWorkspace from './WordImportWorkspace'

vi.mock('./WordDocumentPreview', () => ({ default: ({ file, children }) => <div><span>Preview: {file.name}</span>{children}</div> }))
vi.mock('../../api', () => ({ previewWordUpload: vi.fn() }))
afterEach(cleanup)
const file = new File(['word'], 'sample.docx')

it('shows the upload while detection is still running', () => {
  render(<WordImportWorkspace file={file} fields={[]} />)
  expect(screen.getByText('Preview: sample.docx')).toBeVisible()
  expect(screen.getByText('Rendering the document and detecting fields…')).toBeVisible()
})
it('makes a real source text selection into a field without saving the template first', () => {
  const add = vi.fn()
  render(<WordImportWorkspace file={file} analysis={{ extracted_text: 'Dear Ada Lovelace' }} fields={[]} onAddField={add} />)
  const content = screen.getByLabelText('Select source text')
  const range = document.createRange()
  range.setStart(content.firstChild, 5)
  range.setEnd(content.firstChild, 17)
  const selection = window.getSelection()
  selection.removeAllRanges()
  selection.addRange(range)
  fireEvent.mouseUp(content)
  fireEvent.click(screen.getByRole('button', { name: 'Make selection a field' }))
  expect(add).toHaveBeenCalledWith('Ada Lovelace')
})
it('shows detected source text and edits the selected field’s label and inclusion', () => {
  const change = vi.fn()
  const fields = [{ name: 'client', label: 'Client', source_text: 'Ada Lovelace', confidence: 0.5 }, { name: 'date', label: 'Date', source_text: 'September 7', confidence: 1 }]
  render(<WordImportWorkspace file={file} analysis={{ extracted_text: 'Dear Ada Lovelace' }} fields={fields} onFieldsChange={change} />)
  expect(screen.getByText('2 detected or added fields · 1 need review')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Select Date' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Imported field label' }), { target: { value: 'Signing date' } })
  expect(change).toHaveBeenLastCalledWith([fields[0], { ...fields[1], label: 'Signing date' }])
  fireEvent.click(screen.getByLabelText('Include this field'))
  expect(change).toHaveBeenLastCalledWith([fields[0], { ...fields[1], included: false }])
})

it('rejects a selection spanning paragraphs before it can create a broken mapping', () => {
  const add = vi.fn()
  render(<WordImportWorkspace file={file} analysis={{ extracted_text: 'First paragraph\nSecond paragraph' }} fields={[]} onAddField={add} />)
  const source = screen.getByLabelText('Select source text')
  const range = document.createRange()
  range.selectNodeContents(source)
  window.getSelection().removeAllRanges()
  window.getSelection().addRange(range)
  fireEvent.mouseUp(source)
  expect(screen.getByRole('button', { name: 'Make selection a field' })).toBeDisabled()
  expect(add).not.toHaveBeenCalled()
})

it('preserves choice-field types and respects source review confirmation', () => {
  render(<WordImportWorkspace file={file} analysis={{ extracted_text: 'Yes' }} fields={[{ name: 'answer', label: '', field_type: 'checkbox', docx_choice: { group: 'g', option: 'Yes' }, review_required: true }]} reviewConfirmed />)
  expect(screen.getByLabelText('Imported field type')).toBeDisabled()
  expect(screen.getByLabelText('Imported field label')).toHaveValue('')
  expect(screen.getByText('Reviewed against source')).toBeVisible()
})
