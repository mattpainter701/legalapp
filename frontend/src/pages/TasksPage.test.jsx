import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TasksPage from './TasksPage'
import { getOverdueTasks, getTaskBoard, getTaskBoardConfig, getTasks, sendTaskReminder } from '../api'

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

    expect(await screen.findByText('Return intake call')).toBeInTheDocument()
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
})
