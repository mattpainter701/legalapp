import React, { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { ConfirmProvider, useConfirm } from './ConfirmProvider'

function Harness() {
  const confirm = useConfirm()
  const [result, setResult] = useState('pending')
  return <><button onClick={async () => setResult(String(await confirm({ title: 'Delete record?', message: 'This is permanent.', destructive: true })))}>Open</button><output>{result}</output></>
}

  afterEach(() => cleanup())

describe('ConfirmProvider', () => {
  it('requires an explicit branded dialog decision', async () => {
    const user = userEvent.setup()
    render(<ConfirmProvider><Harness /></ConfirmProvider>)
    await user.click(screen.getByRole('button', { name: 'Open' }))
    expect(screen.getByRole('alertdialog', { name: 'Delete record?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByText('false')).toBeInTheDocument()
  })
})

  it('traps focus, closes on Escape, and restores the trigger', async () => {
    const user = userEvent.setup()
    render(<ConfirmProvider><Harness /></ConfirmProvider>)
    const trigger = screen.getByRole('button', { name: 'Open' })

    await user.click(trigger)
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    const confirmButton = screen.getByRole('button', { name: 'Confirm' })
    expect(cancel).toHaveFocus()

    await user.tab({ shift: true })
    expect(confirmButton).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })
