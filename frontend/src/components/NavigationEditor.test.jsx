import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import NavigationEditor from './NavigationEditor'

afterEach(cleanup)
const items = [{ path: '/intake', label: 'Intake' }, { path: '/tasks', label: 'Tasks' }]
it('saves hiding and order changes and can reset to the role defaults', async () => {
  const user = userEvent.setup(), onSave = vi.fn().mockResolvedValue(), onClose = vi.fn()
  const { unmount } = render(<NavigationEditor items={items} onSave={onSave} onClose={onClose} />)
  await user.click(screen.getByRole('checkbox', { name: 'Intake' }))
  await user.click(screen.getByRole('button', { name: 'Move Tasks up' }))
  expect(screen.getAllByRole('checkbox').map((item) => item.parentElement.textContent)).toEqual(['Tasks', 'Intake'])
  await user.click(screen.getByRole('button', { name: 'Save layout' }))
  expect(onSave).toHaveBeenCalledWith({ hidden: ['/intake'], order: ['/tasks', '/intake'] })
  expect(onClose).toHaveBeenCalledOnce()
  unmount()
  render(<NavigationEditor items={items} preferences={onSave.mock.calls[0][0]} onSave={onSave} onClose={onClose} />)
  await user.click(screen.getByRole('button', { name: 'Reset to role defaults' }))
  expect(screen.getByRole('checkbox', { name: 'Intake' })).toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Save layout' }))
  expect(onSave).toHaveBeenLastCalledWith({ hidden: [], order: [] })
})
it('retains the draft on failed saves and cancel makes no write', async () => {
  const user = userEvent.setup(), onSave = vi.fn().mockRejectedValue(new Error()), onClose = vi.fn()
  render(<NavigationEditor items={items} onSave={onSave} onClose={onClose} />)
  await user.click(screen.getByRole('checkbox', { name: 'Tasks' }))
  await user.click(screen.getByRole('button', { name: 'Save layout' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not save'))
  expect(screen.getByRole('checkbox', { name: 'Tasks' })).not.toBeChecked()
  expect(onClose).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onClose).toHaveBeenCalledOnce()
  expect(onSave).toHaveBeenCalledOnce()
})
