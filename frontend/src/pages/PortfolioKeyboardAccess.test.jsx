import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MatterCard, MatterPortfolioRow, MyMatterRow } from './MatterPortfolioPage'
import { MatterConversationLink } from './MatterDetailPage'
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

  it('uses the native matter title link as the card navigation control', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <MemoryRouter>
        <MatterCard
          m={matter}
          onToggleActive={vi.fn()}
          togglingId={null}
          showAlert={false}
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    expect(link).toHaveAttribute('href', '/matters/matter-1')
    expect(link).toHaveClass('min-h-[44px]', 'w-full')
    expect(container.querySelector('[role="link"]')).not.toBeInTheDocument()
    expect(container.querySelector('[tabindex]')).not.toBeInTheDocument()
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('keeps matter-card navigation and assignment as sibling native controls', async () => {
    const user = userEvent.setup()
    const onToggleActive = vi.fn()
    render(
      <MemoryRouter>
        <MatterCard
          m={{ ...matter, my_assignment_id: 'assignment-1', is_active_working: false }}
          onToggleActive={onToggleActive}
          togglingId={null}
          showAlert={false}
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    const toggle = screen.getByRole('button', { name: 'Set Active' })
    expect(link).not.toContainElement(toggle)
    expect(toggle).toHaveClass('min-h-[44px]', 'min-w-[44px]')

    await user.tab()
    expect(link).toHaveFocus()
    await user.tab()
    expect(toggle).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onToggleActive).toHaveBeenCalledTimes(1)
  })

  it('gives matter cloud-folder links a touch-sized native target', () => {
    render(
      <MemoryRouter>
        <MatterCard
          m={{
            ...matter,
            cloud_folder: { onedrive: { url: 'https://files.example/matter-1' } },
          }}
          onToggleActive={vi.fn()}
          togglingId={null}
          showAlert={false}
        />
      </MemoryRouter>,
    )

    const cloudLink = screen.getByRole('link', { name: 'OneDrive' })
    expect(cloudLink).toHaveAttribute('href', 'https://files.example/matter-1')
    expect(cloudLink).toHaveClass('min-h-[44px]', 'min-w-[44px]')
  })

  it('uses the native matter title link for list-row navigation', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MyMatterRow
          m={matter}
          onToggleActive={vi.fn()}
          togglingId={null}
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    expect(link).toHaveAttribute('href', '/matters/matter-1')
    expect(link).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('uses the native matter title link for table-row navigation', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <MatterPortfolioRow matter={matter} />
          </tbody>
        </table>
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Acme contract review' })
    expect(link).toHaveAttribute('href', '/matters/matter-1')
    expect(link).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('uses a native case-name link for mediation table navigation', async () => {
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
            />
          </tbody>
        </table>
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Rivera mediation' })
    expect(link).toHaveAttribute('href', '/plugins/mediation/cases/mediation-1')
    expect(link).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    await user.tab()
    expect(link).toHaveFocus()
  })

  it('uses a native link for a matter conversation result', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MatterConversationLink
          conversation={{ id: 'conversation-1', title: 'Client strategy' }}
          cloudConnected
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: /Client strategy/ })
    expect(link).toHaveAttribute('href', '/chat?conv=conversation-1')
    await user.tab()
    expect(link).toHaveFocus()
  })
})
