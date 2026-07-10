import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TasksPage from './TasksPage'
import { getOverdueTasks, getTasks } from '../api'

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
  getTask: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  deleteTask: vi.fn(),
  getOverdueTasks: vi.fn(),
  sendTaskReminder: vi.fn(),
  qualifyIntakeTask: vi.fn(),
  markTaskViewed: vi.fn(() => Promise.resolve()),
  markTaskContacted: vi.fn(),
  searchUsers: vi.fn(),
  getLead: vi.fn(),
  convertLead: vi.fn(),
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
    getTasks.mockResolvedValue({ items: [task] })
    getOverdueTasks.mockResolvedValue({ items: [] })
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
    expect(reassign.className).toContain('group-focus-within:opacity-100')
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
})
