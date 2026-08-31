import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RolesTab from './RolesTab'

vi.mock('../../api', () => ({
  listRoles: vi.fn(() => Promise.resolve([])),
  createRole: vi.fn(),
  deleteRole: vi.fn(),
}))

describe('RolesTab workflow capabilities', () => {
  afterEach(cleanup)

  it('lets an administrator grant authoring and approval separately', async () => {
    render(<RolesTab />)

    expect(await screen.findByLabelText('manage_workflows')).toBeInTheDocument()
    expect(screen.getByLabelText('approve_legal_work')).toBeInTheDocument()
    expect(screen.getByLabelText('manage_workflows')).not.toBeChecked()
    expect(screen.getByLabelText('approve_legal_work')).not.toBeChecked()
  })
})
