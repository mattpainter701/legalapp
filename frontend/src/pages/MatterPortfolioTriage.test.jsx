import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { MatterCard, MatterPortfolioRow } from './MatterPortfolioPage'

afterEach(cleanup)

const matter = (overrides = {}) => ({
  id: 'matter-1',
  matter_name: 'Acme contract review',
  matter_number: 'ACME0007',
  client_name: 'Acme Corp',
  status: 'active',
  risk_level: 'low',
  ...overrides,
})

function card(m) {
  return render(
    <MemoryRouter>
      <MatterCard m={m} onToggleActive={vi.fn()} togglingId={null} showAlert={false} />
    </MemoryRouter>,
  )
}

it('shows the quotable matter number on the board card', () => {
  card(matter())
  expect(screen.getByText('ACME0007')).toBeInTheDocument()
  expect(screen.getByText('Acme Corp')).toBeInTheDocument()
})

it('leaves the card readable for a matter with no number yet', () => {
  card(matter({ matter_number: null }))
  expect(screen.getByRole('link', { name: /Acme contract review/ })).toBeInTheDocument()
  expect(screen.getByText('Acme Corp')).toBeInTheDocument()
})

it('shows the matter number in the list row', () => {
  render(
    <MemoryRouter>
      <table><tbody>
        <MatterPortfolioRow matter={matter()} />
      </tbody></table>
    </MemoryRouter>,
  )
  expect(screen.getByText('ACME0007')).toBeInTheDocument()
})

it('renders a dash where a list row has no matter number', () => {
  render(
    <MemoryRouter>
      <table><tbody>
        <MatterPortfolioRow matter={matter({ matter_number: null })} />
      </tbody></table>
    </MemoryRouter>,
  )
  expect(screen.queryByText('ACME0007')).not.toBeInTheDocument()
})
