import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AppShell, { useAppShell } from './AppShell'
import { getConversations, getDocuments } from '../api'

const auth = vi.hoisted(() => ({ modules: ['chat', 'tasks', 'matters'] }))
vi.mock('../App', () => ({
  // Session refreshes may return a new array with the same permissions.
  useAuth: () => ({ user: { id: 'user-1', enabled_modules: auth.modules && [...auth.modules] } }),
}))
vi.mock('../api', () => ({
  getConversations: vi.fn().mockResolvedValue([]),
  getDocuments: vi.fn().mockResolvedValue([]),
  createConversation: vi.fn(), deleteConversation: vi.fn(),
  deleteDocument: vi.fn(), logout: vi.fn(),
}))
vi.mock('./dialog/ConfirmProvider', () => ({ useConfirm: () => vi.fn() }))
vi.mock('./Sidebar', () => ({ default: () => null }))

function Workspace() {
  const { conversations, documents } = useAppShell()
  const navigate = useNavigate()
  return <>
    <button onClick={() => navigate('/chat')}>Open Assistant</button>
    <button onClick={() => navigate('/tasks')}>Open Tasks</button>
    <output aria-label="Conversations">{conversations.map((item) => item.title).join(', ')}</output>
    <output aria-label="Documents">{documents.map((item) => item.filename).join(', ')}</output>
  </>
}

function mount(path = '/tasks') {
  return render(<MemoryRouter initialEntries={[path]}><AppShell><Workspace /></AppShell></MemoryRouter>)
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  auth.modules = ['chat', 'tasks', 'matters']
})

describe('Assistant data loading', () => {
  it('defers both lists until Assistant opens and does not refetch on shell rerenders', async () => {
    getConversations.mockResolvedValueOnce([{ id: 'c1', title: 'Existing discussion' }])
    getDocuments.mockResolvedValueOnce({ documents: [{ id: 'd1', filename: 'Evidence.pdf' }] })
    mount()
    expect(getConversations).not.toHaveBeenCalled()
    expect(getDocuments).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Open Assistant' }))
    expect(await screen.findByText('Existing discussion')).toBeInTheDocument()
    expect(screen.getByText('Evidence.pdf')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open sidebar' }))
    await act(async () => {})
    expect(getConversations).toHaveBeenCalledTimes(1)
    expect(getDocuments).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Open Tasks' }))
    expect(screen.getByLabelText('Conversations')).toBeEmptyDOMElement()
    expect(getConversations).toHaveBeenCalledTimes(1)
  })

  it('keeps conversations available when documents fail', async () => {
    getConversations.mockResolvedValueOnce([{ id: 'c1', title: 'Available conversation' }])
    getDocuments.mockRejectedValueOnce(new Error('Document service unavailable'))
    mount('/chat')
    expect(await screen.findByText('Available conversation')).toBeInTheDocument()
    expect(getConversations).toHaveBeenCalledTimes(1)
  })

  it('keeps documents available when conversations fail', async () => {
    getConversations.mockRejectedValueOnce(new Error('Conversation service unavailable'))
    getDocuments.mockResolvedValueOnce([{ id: 'd1', filename: 'Available.pdf' }])
    mount('/chat')
    expect(await screen.findByText('Available.pdf')).toBeInTheDocument()
  })

  it('ignores results that arrive after leaving Assistant', async () => {
    let finishConversations
    let finishDocuments
    getConversations.mockReturnValueOnce(new Promise((resolve) => { finishConversations = resolve }))
    getDocuments.mockReturnValueOnce(new Promise((resolve) => { finishDocuments = resolve }))
    mount('/chat')
    fireEvent.click(screen.getByRole('button', { name: 'Open Tasks' }))
    await act(async () => {
      finishConversations([{ id: 'c1', title: 'Late conversation' }])
      finishDocuments([{ id: 'd1', filename: 'Late.pdf' }])
    })
    expect(screen.getByLabelText('Conversations')).toBeEmptyDOMElement()
    expect(screen.getByLabelText('Documents')).toBeEmptyDOMElement()
  })

  it('does not request chat data without module access', async () => {
    auth.modules = undefined
    mount('/chat')
    await waitFor(() => expect(screen.getByLabelText('Documents')).toBeEmptyDOMElement())
    expect(getConversations).not.toHaveBeenCalled()
    expect(getDocuments).not.toHaveBeenCalled()
  })
})
