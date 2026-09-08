import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyTemplate } from '../../api'
import TemplateCopyAction from './TemplateCopyAction'

vi.mock('../../api', () => ({ copyTemplate: vi.fn() }))

describe('template variations', () => {
  afterEach(cleanup)
  it('creates a named copy and opens the returned draft', async () => {
    const onCreated = vi.fn()
    copyTemplate.mockResolvedValueOnce({ id: 'new-draft' })
    render(<TemplateCopyAction template={{ id: 'master', title: 'Agreement' }} onCreated={onCreated} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create a variation' }))
    fireEvent.change(screen.getByLabelText('New template name'), { target: { value: '  Flat fee agreement  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create separate template' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 'new-draft' }))
    expect(copyTemplate).toHaveBeenCalledWith('master', { title: 'Flat fee agreement' })
    expect(screen.queryByLabelText('New template name')).not.toBeInTheDocument()
  })
  it('keeps a failed copy open with its entered name and a retry', async () => {
    copyTemplate.mockRejectedValueOnce(new Error('offline'))
    render(<TemplateCopyAction template={{ id: 'master', title: 'Agreement' }} onCreated={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create a variation' }))
    fireEvent.change(screen.getByLabelText('New template name'), { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Create separate template' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('New template name'), { target: { value: 'New name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create separate template' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be created')
    expect(screen.getByLabelText('New template name')).toHaveValue('New name')
  })
  it('retries navigation without creating another copy', async () => {
    copyTemplate.mockClear().mockResolvedValueOnce({ id: 'created' })
    const onCreated = vi.fn().mockRejectedValueOnce(new Error('reload failed')).mockResolvedValueOnce()
    render(<TemplateCopyAction template={{ id: 'master', title: 'Agreement' }} onCreated={onCreated} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create a variation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create separate template' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('was created')
    fireEvent.click(screen.getByRole('button', { name: 'Open created template' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(2))
    expect(copyTemplate).toHaveBeenCalledTimes(1)
  })
})
