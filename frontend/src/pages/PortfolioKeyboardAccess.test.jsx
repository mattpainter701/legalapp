import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MatterCard, MatterPortfolioRow, MyMatterRow } from './MatterPortfolioPage'
import { MediationCaseRow } from './MediationPortfolioPage'

const matter = {
  id: 'matter-1',
  matter_name: 'Acme contract review',
  client_name: 'Acme Corp',
  status: 'active',
  risk_level: 'low',
}

describe('portfolio keyboard navigation', () => {
  afterEach(() => cleanup())

  it('exposes matter card navigation as a native link', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MatterCard
          m={matter}
          onNavigate={vi.fn()}
          onToggleActive={vi.fn()}
          togglingId={null}
          showAlert={false}
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    expect(link).toHaveAttribute('href', '/matters/matter-1')
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('exposes matter list-row navigation as a native link', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MyMatterRow
          m={matter}
          onNavigate={vi.fn()}
          onToggleActive={vi.fn()}
          togglingId={null}
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    expect(link).toHaveAttribute('href', '/matters/matter-1')
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('exposes the full portfolio table row as a native matter link', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <MatterPortfolioRow matter={matter} onNavigate={vi.fn()} />
          </tbody>
        </table>
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    expect(link).toHaveAttribute('href', '/matters/matter-1')
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('exposes mediation table-row navigation as a native link', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <MediationCaseRow
              caseRecord={{
                id: 'mediation-1',
                case_name: 'Rivera mediation',
                party_a: 'Rivera',
                party_b: 'Northwind',
                status: 'active',
              }}
              onNavigate={vi.fn()}
            />
          </tbody>
        </table>
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Rivera mediation' })
    expect(link).toHaveAttribute('href', '/plugins/mediation/cases/mediation-1')
    await user.tab()
    expect(link).toHaveFocus()
  })
})
