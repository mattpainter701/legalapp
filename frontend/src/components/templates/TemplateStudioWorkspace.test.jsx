import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import TemplateStudioWorkspace from './TemplateStudioWorkspace'
vi.mock('./TemplateStudioEditor', () => ({ default: ({ onDirtyChange }) => <><button onClick={() => onDirtyChange(true)}>Change field</button><button onClick={() => onDirtyChange(false)}>Save changes</button></> }))
vi.mock('./TemplateCopyAction', () => ({ default: () => <button>Create variation</button> }))
afterEach(cleanup)
function Location() { return <output aria-label="Location">{useLocation().pathname}</output> }
it('focuses the document and protects unsaved fields before switching Studio sections', () => {
  render(<MemoryRouter initialEntries={['/templates/one/studio']}><TemplateStudioWorkspace template={{ id: 'one', title: 'Example', format: 'markdown' }} onDerived={vi.fn()} /><Location /></MemoryRouter>)
  const shell = screen.getByRole('heading', { name: 'Example' }).closest('.studio-shell')
  expect(shell).toHaveAttribute('data-focused', 'true')
  fireEvent.click(screen.getByText('Show app navigation'))
  expect(shell).toHaveAttribute('data-focused', 'false')
  fireEvent.click(screen.getByText('Change field'))
  expect(screen.getByText('Template settings')).toBeDisabled()
  expect(screen.getByText('Preview draft')).toBeDisabled()
  expect(screen.queryByText('Create variation')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('link', { name: 'Test' }))
  expect(screen.getByLabelText('Location')).toHaveTextContent('/templates/one/studio')
  expect(screen.getByRole('alert')).toHaveTextContent('Save your field changes')
  fireEvent.click(screen.getByText('Save changes'))
  fireEvent.click(screen.getByRole('link', { name: 'Test' }))
  expect(screen.getByLabelText('Location')).toHaveTextContent('/templates/one/studio/test')
})
