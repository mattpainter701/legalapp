import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TasksPage from './TasksPage'
import { createTask, getOverdueTasks, getTaskBoard, getTaskBoardConfig, getTasks, sendTaskReminder, updateTask } from '../api'

vi.mock('../App', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      role: 'admin',
      enabled_modules: ['tasks', 'matters'],
    },
  }),
}))

vi.mock('../api', () => ({
  getTasks: vi.fn(),
  getTaskBoard: vi.fn(),
  getTaskBoardConfig: vi.fn(),
  recordTaskBoardTelemetry: vi.fn(() => Promise.resolve({ accepted: true })),
  getTask: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  transitionTask: vi.fn(),
  getTaskEvents: vi.fn(),
  deleteTask: vi.fn(),
  getOverdueTasks: vi.fn(),
  sendTaskReminder: vi.fn(),
  qualifyIntakeTask: vi.fn(),
  markTaskViewed: vi.fn(() => Promise.resolve()),
  markTaskContacted: vi.fn(),
  searchUsers: vi.fn(),
  getLead: vi.fn(),
  convertLead: vi.fn(),
  getMatterFieldOptions: vi.fn(() => Promise.resolve({})),
  getMattersV2: vi.fn(() => Promise.resolve({ items: [] })),
  getContacts: vi.fn(),
}))

const task = {
  id: 'task-1',
  title: 'Return intake call',
  description: 'Call the prospective client before noon.',
  status: 'pending',
  priority: 'high',
  task_type: 'call',
  due_date: null,
  assigned_to_user_id: null,
  contact_id: null,
  source: null,
  external_ref: null,
  version: 7,
}

describe('TasksPage accessibility', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    getTasks.mockResolvedValue({ items: [task] })
    getOverdueTasks.mockResolvedValue({ items: [] })
    getTaskBoardConfig.mockResolvedValue({ enabled: true })
    getTaskBoard.mockResolvedValue({
      scope: 'mine',
      generated_at: '2026-08-04T12:00:00Z',
      risk_counts: { overdue: 0, due_today: 0, unassigned: 0, waiting_follow_up_due: 0 },
      columns: [
        { status: 'pending', label: 'To Do', total: 1, items: [{ ...task, version: 1, status_changed_at: '2026-08-04T12:00:00Z', updated_at: '2026-08-04T12:00:00Z' }], next_cursor: null },
        { status: 'in_progress', label: 'In Progress', total: 0, items: [], next_cursor: null },
        { status: 'waiting', label: 'Waiting', total: 0, items: [], next_cursor: null },
        { status: 'review', label: 'Review', total: 0, items: [], next_cursor: null },
        { status: 'completed', label: 'Done', total: 0, items: [], next_cursor: null },
      ],
    })
  })

  afterEach(() => cleanup())

  it('names filters and row actions and exposes hover actions to keyboard focus without axe violations', async () => {
    const { container } = render(<MemoryRouter><TasksPage /></MemoryRouter>)

    const taskTitle = await screen.findByText('Return intake call')
    expect(taskTitle).toBeInTheDocument()
    const taskRow = taskTitle.closest('[id^="task-"]')
    expect(taskRow).toHaveClass('grid', 'grid-cols-[auto_minmax(0,1fr)]', 'sm:flex')
    expect(taskTitle).toHaveClass('block', 'break-words')
    expect(taskTitle.parentElement?.nextElementSibling).toHaveClass('col-start-2', 'flex-wrap', 'sm:flex-nowrap')
    expect(screen.getByRole('combobox', { name: 'Filter tasks by status' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Filter tasks by priority' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Filter tasks by type' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Complete task: Return intake call' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Send reminder for Return intake call' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete task: Return intake call' })).toBeInTheDocument()

    const reassign = screen.getByRole('button', { name: 'Reassign' })
    expect(reassign.className).toContain('opacity-100')
    expect(reassign.className).toContain('sm:group-focus-within:opacity-100')
    expect(reassign.className).toContain('focus:opacity-100')
    reassign.focus()
    expect(reassign).toHaveFocus()

    expect(await axe(container)).toHaveNoViolations()
  })

  it('traps modal focus and restores it to the trigger when Escape closes the dialog', async () => {
    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')

    const trigger = screen.getByRole('button', { name: 'New Task' })
    await user.click(trigger)

    const dialog = screen.getByRole('dialog', { name: 'New Task' })
    expect(screen.getByRole('textbox', { name: 'Task title' })).toHaveFocus()
    expect(screen.getByRole('combobox', { name: 'Task type' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Task priority' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Linked contact' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Task notes' })).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()

    const closeButton = screen.getByRole('button', { name: 'Close dialog' })
    closeButton.focus()
    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(screen.getByRole('button', { name: 'Create Task' })).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(dialog).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('applies Escape close and focus restoration to task action dialogs', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')

    const trigger = screen.getByRole('button', { name: 'Reassign' })
    await user.click(trigger)
    expect(screen.getByRole('dialog', { name: 'Reassign Task' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Reassign Task' })).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('shows the API email-readiness error instead of claiming a reminder was sent', async () => {
    const user = userEvent.setup()
    sendTaskReminder.mockRejectedValueOnce({
      response: {
        status: 503,
        data: {
          detail: 'Task reminder was not completed because outbound email is unavailable. Ask an administrator to enable and verify the SMTP configuration.',
        },
      },
    })
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')

    await user.click(screen.getByRole('button', { name: 'Send reminder for Return intake call' }))

    expect(await screen.findByText('Not sent')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('outbound email is unavailable')
    expect(screen.queryByText('Sent!')).not.toBeInTheDocument()
  })

  it('switches between the deadline list and scoped firm board', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')

    await user.click(screen.getByRole('button', { name: 'Board' }))
    expect(await screen.findByRole('heading', { name: 'To Do' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'My Work' })).toHaveAttribute('aria-pressed', 'true')
    expect(getTaskBoard).toHaveBeenCalledWith(expect.objectContaining({ scope: 'mine' }))

    await user.click(screen.getByRole('button', { name: 'Firm Work' }))
    await waitFor(() => expect(getTaskBoard).toHaveBeenCalledWith(expect.objectContaining({ scope: 'firm' })))
    expect(screen.getByText('Firm workflow')).toBeInTheDocument()
  })

  it('keeps the deadline list available when the tenant disables the board', async () => {
    window.localStorage.setItem('tasks:view-mode', 'board')
    getTaskBoardConfig.mockResolvedValueOnce({ enabled: false })
    render(<MemoryRouter><TasksPage /></MemoryRouter>)

    expect(await screen.findByText('Return intake call')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Board' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
    expect(getTaskBoard).not.toHaveBeenCalled()
  })

  it('carries a matter deep-link into newly created tasks', async () => {
    const user = userEvent.setup()
    createTask.mockResolvedValueOnce({ ...task, id: 'task-2', matter_id: 'matter-7' })
    render(<MemoryRouter initialEntries={['/tasks?matter_id=matter-7']}><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')
    await user.click(screen.getByRole('button', { name: 'New Task' }))
    await user.type(screen.getByRole('textbox', { name: 'Task title' }), 'File status report')
    await user.click(screen.getByRole('button', { name: 'Create Task' }))
    await waitFor(() => expect(createTask).toHaveBeenCalledWith(expect.objectContaining({
      title: 'File status report', matter_id: 'matter-7',
    })))
  })

  it('edits task details and clears a due date explicitly', async () => {
    const user = userEvent.setup()
    getTasks.mockResolvedValueOnce({ items: [{ ...task, due_date: '2099-01-02', due_time: '14:00:00' }] })
    updateTask.mockResolvedValueOnce({ ...task, title: 'Updated task', due_date: null })
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')
    await user.click(screen.getByRole('button', { name: 'Edit task: Return intake call' }))
    const title = screen.getByRole('textbox', { name: 'Task title' })
    await user.clear(title)
    await user.type(title, 'Updated task')
    fireEvent.change(screen.getByLabelText('Task due date'), { target: { value: '' } })
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith('task-1', expect.objectContaining({ title: 'Updated task', due_date: null, due_time: null })))
  })

  it('keeps edit inputs and shows the API error when correction fails', async () => {
    const user = userEvent.setup()
    updateTask.mockRejectedValueOnce({ response: { data: { detail: 'Task is locked' } } })
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')
    await user.click(screen.getByRole('button', { name: 'Edit task: Return intake call' }))
    const title = screen.getByRole('textbox', { name: 'Task title' })
    await user.clear(title)
    await user.type(title, 'Corrected title')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Task is locked')
    expect(screen.getByRole('textbox', { name: 'Task title' })).toHaveValue('Corrected title')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled()
  })

  it('keeps the editor open during a pending save and sends the task version once', async () => {
    const user = userEvent.setup()
    let resolveSave
    updateTask.mockReturnValueOnce(new Promise((resolve) => { resolveSave = resolve }))
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')
    await user.click(screen.getByRole('button', { name: 'Edit task: Return intake call' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    fireEvent.click(screen.getByRole('button', { name: 'Saving…' }))
    await user.keyboard('{Escape}')
    expect(updateTask).toHaveBeenCalledTimes(1)
    expect(updateTask).toHaveBeenCalledWith('task-1', expect.objectContaining({ expected_version: 7 }))
    expect(screen.getByRole('dialog', { name: 'Edit Task' })).toBeInTheDocument()
    await act(async () => resolveSave({ ...task, title: task.title }))
  })

  it('does not crash when the edit API returns an object error detail', async () => {
    const user = userEvent.setup()
    updateTask.mockRejectedValueOnce({ response: { data: { detail: { message: 'Task locked' } } } })
    render(<MemoryRouter><TasksPage /></MemoryRouter>)
    await screen.findByText('Return intake call')
    await user.click(screen.getByRole('button', { name: 'Edit task: Return intake call' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Task locked')
  })

  it.each(['resolve', 'reject'])('keeps the current matter list after an earlier request %s', async (outcome) => {
    let settleA
    getTasks.mockImplementation(({ matter_id }) => matter_id === 'A'
      ? new Promise((resolve, reject) => { settleA = outcome === 'resolve' ? resolve : reject })
      : Promise.resolve({ items: [{ ...task, id: 'B-task', title: 'Current B task' }] }))
    render(<MemoryRouter initialEntries={['/tasks?matter_id=A']}><Link to="/tasks?matter_id=B">Switch matter</Link><TasksPage /></MemoryRouter>)
    await waitFor(() => expect(getTasks).toHaveBeenCalledWith(expect.objectContaining({ matter_id: 'A' })))
    fireEvent.click(screen.getByRole('link', { name: 'Switch matter' }))
    await screen.findByText('Current B task')
    expect(getTasks).toHaveBeenLastCalledWith(expect.objectContaining({ matter_id: 'B' }))
    expect(screen.getByLabelText('Filter tasks by matter')).toHaveValue('B')
    await act(async () => settleA(outcome === 'resolve' ? { items: [{ ...task, title: 'Old A task' }] } : new Error('old failure')))
    expect(screen.getByText('Current B task')).toBeInTheDocument()
    expect(screen.queryByText('Old A task')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
