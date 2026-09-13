import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../api'
import FirmEmailIntake, { contactFile } from './FirmEmailIntake'

vi.mock('../api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
const settings = { enabled: true, alias: { address: 'f-firm@intake.example.com' }, timezone: 'America/Chicago', pending_count: 1,
  staff: [{ id: 'jane', name: 'Jane Smith', email: 'jane@example.com' }] }
const queued = { items: [{ id: 'email', subject: '[TASK] Jane, review this tomorrow', sender: 'owner@example.com', body_preview: 'Client email',
  suggestion: { sender: 'owner@example.com', task: { title: 'review this', due_date: '2026-09-14', assigned_to_user_id: 'jane' }, matters: [{ id: 'matter', title: 'Smith case' }] } }],
  matters: [{ id: 'matter', title: 'Smith case' }] }
const show = (props) => render(<MemoryRouter><FirmEmailIntake {...props} /></MemoryRouter>)
afterEach(cleanup)
beforeEach(() => {
  vi.resetAllMocks()
  api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith('/queue') ? queued : settings }))
  api.post.mockResolvedValue({ data: settings })
})

describe('firm email intake', () => {
  it('shows a phone-friendly contact and review count, with a dismissible tip', async () => {
    show()
    expect(await screen.findByText('f-firm@intake.example.com')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Needs review (1)' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss tip' }))
    expect(screen.queryByText('f-firm@intake.example.com')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show forwarding tip' }))
    expect(screen.getByRole('button', { name: 'Save LawHand contact' })).toBeVisible()
    expect(contactFile('firm@example.com')).toContain('FN:LawHand\r\nN:LawHand;;;;\r\nEMAIL;TYPE=INTERNET:firm@example.com')
  })
  it('reviews a suggested to-do before creating it', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Needs review (1)' }))
    await screen.findByText('[TASK] Jane, review this tomorrow', { selector: 'h4' })
    expect(screen.getByLabelText('Matter')).toHaveValue('matter')
    expect(screen.getByLabelText('Assign to')).toHaveValue('jane')
    api.post.mockResolvedValue({ data: { task_id: 'todo', matter_id: 'matter' } })
    await userEvent.click(screen.getByRole('button', { name: 'File + create to-do' }))
    expect(api.post).toHaveBeenCalledWith('/firm-email-intake/queue/email/accept', {
      title: 'review this', matter_id: 'matter', assigned_to_user_id: 'jane', due_date: '2026-09-14',
    })
    expect(await screen.findByRole('link', { name: 'Open to-do' })).toHaveAttribute('href', '/tasks/todo')
  })
  it('requires a choice for ambiguous matter and assignee, and permits an undated to-do', async () => {
    const unresolved = { ...queued, items: [{ ...queued.items[0], suggestion: { task: { title: 'Review', assignee_hint: 'Jane' }, matters: [] } }] }
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith('/queue') ? unresolved : settings }))
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Needs review (1)' }))
    expect(await screen.findByRole('button', { name: 'File + create to-do' })).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Matter'), 'matter')
    await userEvent.selectOptions(screen.getByLabelText('Assign to'), 'jane')
    await userEvent.click(screen.getByRole('button', { name: 'File + create to-do' }))
    expect(api.post.mock.calls[0][1].due_date).toBeNull()
  })
  it('requires explicit confirmation before rejection', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Needs review (1)' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    expect(api.post).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm rejection' }))
    expect(api.post).toHaveBeenCalledWith('/firm-email-intake/queue/email/reject')
  })
  it('enables the address in admin, and confirms replacement', async () => {
    api.get.mockResolvedValue({ data: { ...settings, alias: null } })
    show({ admin: true })
    await userEvent.click(await screen.findByRole('button', { name: 'Enable firm address' }))
    expect(api.post.mock.calls[0][1].action).toBe('enable')
    await userEvent.click(await screen.findByRole('button', { name: 'Replace address' }))
    expect(api.post).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))
    expect(api.post.mock.calls[1][1]).toEqual({ action: 'rotate', timezone: expect.any(String) })
  })
  it('shows errors without losing the request', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Needs review (1)' }))
    await screen.findByLabelText('Matter')
    api.post.mockRejectedValue({ response: { data: { detail: 'Choose an active member of your firm' } } })
    await userEvent.click(screen.getByRole('button', { name: 'File + create to-do' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose an active member')
    expect(screen.getByLabelText('To-do')).toHaveValue('review this')
  })
  it('reports loading errors and allows retry', async () => {
    api.get.mockRejectedValueOnce(new Error('offline'))
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be loaded')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByText('f-firm@intake.example.com')
  })
  it('hides the unused matters tip but retains the disabled-deployment admin explanation', async () => {
    api.get.mockResolvedValue({ data: { ...settings, alias: null, pending_count: 0, enabled: false } })
    const { unmount } = show()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: 'Email to-dos' })).not.toBeInTheDocument()
    unmount()
    show({ admin: true })
    expect(await screen.findByText(/not configured on this deployment/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Enable firm address' })).toBeDisabled()
  })
})
