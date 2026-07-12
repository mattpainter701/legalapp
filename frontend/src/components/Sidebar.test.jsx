import React, { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Sidebar from './Sidebar'

const userRecord = {
  id: 'user-1',
  role: 'user',
  full_name: 'Intake User',
  email: 'intake@example.test',
  billing_tier: 'intake',
  enabled_modules: ['tasks', 'intake-dashboard'],
  upsell_target: 'full-platform',
}

function Harness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open navigation</button>
      <Sidebar user={userRecord} isOpen={open} onClose={() => setOpen(false)} onLogout={() => {}} />
    </>
  )
}

describe('Sidebar mobile drawer', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('uses modal semantics, closes on Escape, and restores the menu trigger', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const user = userEvent.setup()
    render(<MemoryRouter><Harness /></MemoryRouter>)
    const trigger = screen.getByRole('button', { name: 'Open navigation' })
    await user.click(trigger)
    expect(screen.getByRole('dialog', { name: 'Workspace navigation' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close sidebar' })).toHaveFocus())

    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Sign out' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close sidebar' })).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('hands the focus trap to the upgrade dialog and restores its drawer trigger', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const user = userEvent.setup()
    render(<MemoryRouter><Harness /></MemoryRouter>)

    await user.click(screen.getByRole('button', { name: 'Open navigation' }))
    const upgradeTrigger = screen.getByRole('button', { name: 'Explore the full platform' })
    await user.click(upgradeTrigger)

    expect(screen.getByRole('dialog', { name: 'Upgrade to the full platform' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Workspace navigation' })).not.toBeInTheDocument()
    const close = screen.getByRole('button', { name: 'Close upgrade dialog' })
    await waitFor(() => expect(close).toHaveFocus())

    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Request upgrade' })).toHaveFocus()
    await user.tab()
    expect(close).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Upgrade to the full platform' })).not.toBeInTheDocument())
    await waitFor(() => expect(upgradeTrigger).toHaveFocus())
    expect(screen.getByRole('dialog', { name: 'Workspace navigation' })).toBeInTheDocument()
  })
})
