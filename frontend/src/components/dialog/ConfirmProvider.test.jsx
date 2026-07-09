import React, { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ConfirmProvider, useConfirm } from './ConfirmProvider'

function Harness() {
  const confirm = useConfirm()
  const [result, setResult] = useState('pending')
  return <><button onClick={async () => setResult(String(await confirm({ title: 'Delete record?', message: 'This is permanent.', destructive: true })))}>Open</button><output>{result}</output></>
}

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
