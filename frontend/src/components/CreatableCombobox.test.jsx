import React, { useState } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import CreatableCombobox from './CreatableCombobox'

function Example() {
  const [value, setValue] = useState('')
  return (
    <div>
      <label htmlFor="matter-type">Matter Type</label>
      <CreatableCombobox
        id="matter-type"
        value={value}
        onChange={setValue}
        options={['Divorce', 'Litigation']}
      />
      <output>{value}</output>
    </div>
  )
}

describe('CreatableCombobox', () => {
  afterEach(() => cleanup())

  it('offers existing values and accepts a new value', async () => {
    const user = userEvent.setup()
    render(<Example />)

    const input = screen.getByRole('combobox', { name: 'Matter Type' })
    await user.click(input)
    expect(screen.getByRole('option', { name: 'Divorce' })).toBeInTheDocument()

    await user.type(input, 'Space Law')
    const createOption = screen.getByRole('option', { name: /Use new value:.*Space Law/ })
    expect(createOption).toBeInTheDocument()
    await user.click(createOption)

    expect(input).toHaveValue('Space Law')
    expect(screen.getByText('Space Law', { selector: 'output' })).toBeInTheDocument()
  })

  it('filters and selects with the keyboard', async () => {
    const user = userEvent.setup()
    render(<Example />)

    const input = screen.getByRole('combobox', { name: 'Matter Type' })
    await user.type(input, 'Lit')
    expect(screen.getByRole('option', { name: 'Litigation' })).toBeInTheDocument()
    await user.keyboard('{ArrowDown}{Enter}')

    expect(input).toHaveValue('Litigation')
    expect(input).toHaveFocus()
  })
})
