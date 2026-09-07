import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import RolesTab from './RolesTab'
import { createRole, listRoles, updateRole } from '../../api'
import { VIEW_PRESETS } from '../../navigation'

vi.mock('../../api', () => ({ listRoles: vi.fn(), createRole: vi.fn(), updateRole: vi.fn(), deleteRole: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('lets an administrator grant authoring and approval separately', async () => {
  listRoles.mockResolvedValue([])
  render(<RolesTab />)
  expect(await screen.findByLabelText('manage_workflows')).not.toBeChecked()
  expect(screen.getByLabelText('approve_legal_work')).not.toBeChecked()
})
it('creates a receptionist view with the six requested functions', async () => {
  listRoles.mockResolvedValue([]); createRole.mockResolvedValue({})
  const user = userEvent.setup()
  render(<RolesTab />)
  await user.selectOptions(screen.getByRole('combobox', { name: 'View starting point' }), 'Receptionist')
  expect(screen.getByRole('textbox', { name: 'Role name' })).toHaveValue('Receptionist')
  expect(screen.getByRole('checkbox', { name: 'Intake' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: 'Invoices' })).not.toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Create role' }))
  await waitFor(() => expect(createRole).toHaveBeenCalledWith(expect.objectContaining({ name: 'Receptionist', navigation_paths: VIEW_PRESETS.Receptionist, capabilities: [] })))
  expect(await screen.findByRole('status')).toHaveTextContent('Role saved')
})
it('edits an existing role view without losing its capabilities or description', async () => {
  listRoles.mockResolvedValue([{ id: 'r1', name: 'Attorney', description: 'Existing duties', capabilities: ['manage_matters'], navigation_paths: null, is_system: true }])
  updateRole.mockRejectedValueOnce(new Error()).mockResolvedValue({})
  const user = userEvent.setup()
  render(<RolesTab />)
  await user.click(await screen.findByRole('button', { name: 'Edit Attorney' }))
  await user.selectOptions(screen.getByRole('combobox', { name: 'View starting point' }), 'Partner')
  await user.click(screen.getByRole('checkbox', { name: 'Reports' }))
  await user.click(screen.getByRole('button', { name: 'Save role' }))
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: 'Reports' })).not.toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Save role' }))
  await waitFor(() => expect(updateRole).toHaveBeenLastCalledWith('r1', { name: 'Attorney', description: 'Existing duties', capabilities: ['manage_matters'], navigation_paths: VIEW_PRESETS.Partner.filter((path) => path !== '/reports') }))
})
