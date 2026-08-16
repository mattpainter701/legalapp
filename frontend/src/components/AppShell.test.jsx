import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppShell from './AppShell'

vi.mock('../App', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      role: 'user',
      full_name: 'Workspace User',
      enabled_modules: ['intake-dashboard', 'matters', 'chat', 'calendar', 'tasks'],
    },
    logout: vi.fn(),
  }),
}))

vi.mock('../api', () => ({
  getConversations: vi.fn().mockResolvedValue([]),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getDocuments: vi.fn().mockResolvedValue([]),
  uploadDocument: vi.fn(),
  deleteDocument: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('./dialog/ConfirmProvider', () => ({
  useConfirm: () => vi.fn(),
}))

vi.mock('./Sidebar', () => ({
  default: ({ desktopCollapsed, onToggleDesktopCollapsed }) => (
    <button type="button" onClick={onToggleDesktopCollapsed}>
      {desktopCollapsed ? 'Expand mock sidebar' : 'Collapse mock sidebar'}
    </button>
  ),
}))

describe('AppShell responsive layout controls', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('snaps the desktop sidebar width with keyboard controls and remembers the preference', async () => {
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <AppShell title="Tasks"><div>Task workspace</div></AppShell>
      </MemoryRouter>,
    )

    const separator = screen.getByRole('separator', { name: 'Resize workspace navigation' })
    expect(separator).toHaveAttribute('aria-valuenow', '288')

    fireEvent.keyDown(separator, { key: 'ArrowRight' })
    expect(separator).toHaveAttribute('aria-valuenow', '344')
    await waitFor(() => {
      expect(window.localStorage.getItem('clarity.workspace.sidebar-width')).toBe('344')
    })

    fireEvent.keyDown(separator, { key: 'Home' })
    expect(separator).toHaveAttribute('aria-valuenow', '240')
  })

  it('collapses the desktop navigation and keeps the mobile/tablet destination visible', () => {
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <AppShell title="Tasks"><div>Task workspace</div></AppShell>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Collapse mock sidebar' }))
    expect(screen.queryByRole('separator', { name: 'Resize workspace navigation' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Expand mock sidebar' })).toBeInTheDocument()

    const mobileNavigation = screen.getByRole('navigation', { name: 'Primary workspace navigation' })
    expect(within(mobileNavigation).getByRole('button', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page')
  })
})
