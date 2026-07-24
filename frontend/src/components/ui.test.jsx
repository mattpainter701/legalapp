import React, { useState } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import {
  FilterToolbar,
  MetricStrip,
  SegmentedControl,
  Spinner,
  WorkspacePage,
  WorkspacePageHeader,
} from './ui'

afterEach(cleanup)

describe('workspace UI primitives', () => {
  it('provides a consistent accessible page hierarchy and loading state', () => {
    render(
      <WorkspacePage>
        <WorkspacePageHeader
          eyebrow="Firm directory"
          title="Contacts"
          description="People and organizations"
          meta={<span>12 contacts</span>}
          actions={<button type="button">New contact</button>}
        />
        <MetricStrip items={[{ label: 'Open', value: 12 }]} />
        <Spinner />
      </WorkspacePage>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Contacts' })).toBeInTheDocument()
    expect(screen.getByText('12 contacts')).toBeInTheDocument()
    expect(screen.getByText('Open')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Loading' })).toBeInTheDocument()
  })

  it('uses pressed-state semantics for reusable module filters', async () => {
    const user = userEvent.setup()

    function Harness() {
      const [value, setValue] = useState('all')
      return (
        <FilterToolbar ariaLabel="Invoice filters">
          <SegmentedControl
            label="Filter invoices"
            items={[
              { value: 'all', label: 'All' },
              { value: 'overdue', label: 'Overdue' },
            ]}
            value={value}
            onChange={setValue}
          />
        </FilterToolbar>
      )
    }

    render(<Harness />)
    const all = screen.getByRole('button', { name: 'All' })
    const overdue = screen.getByRole('button', { name: 'Overdue' })
    expect(all).toHaveAttribute('aria-pressed', 'true')
    expect(overdue).toHaveAttribute('aria-pressed', 'false')

    await user.click(overdue)
    expect(all).toHaveAttribute('aria-pressed', 'false')
    expect(overdue).toHaveAttribute('aria-pressed', 'true')
  })
})
