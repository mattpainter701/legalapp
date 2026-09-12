import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  MATTER_LIFECYCLE_COLUMNS,
  MatterLifecycleBoard,
  MyMatterRow,
  matterLifecycleStatus,
} from './MatterPortfolioPage'
import {
  MATTER_LIST_COLUMN_DEFS,
  MATTER_LIST_DEFAULT_HIDDEN,
  useMatterListColumns,
} from '../components/matters/MatterListColumns'

afterEach(() => cleanup())

const matter = (overrides = {}) => ({
  id: 'matter-1',
  matter_name: 'Acme contract review',
  matter_number: 'ACME0007',
  client_name: 'Acme Corp',
  attorney_of_record_name: 'Kara Bedingfield',
  partner_attorney_name: 'Richard Mayhew',
  practice_area: 'Commercial',
  status: 'open',
  risk_level: 'low',
  my_assignment_id: 'assignment-1',
  is_active_working: false,
  ...overrides,
})

describe('matterLifecycleStatus', () => {
  it('maps every terminal status to the closed column', () => {
    expect(matterLifecycleStatus({ status: 'closed' })).toBe('closed')
    expect(matterLifecycleStatus({ status: 'settled' })).toBe('closed')
    expect(matterLifecycleStatus({ status: 'dismissed' })).toBe('closed')
  })

  it('keeps threatened matters in the open column', () => {
    expect(matterLifecycleStatus({ status: 'threatened' })).toBe('open')
    expect(matterLifecycleStatus({ status: 'active' })).toBe('active')
    expect(matterLifecycleStatus({ status: 'pending' })).toBe('pending')
  })
})

describe('MatterLifecycleBoard', () => {
  it('renders one column per lifecycle status with its cards', () => {
    render(
      <MemoryRouter>
        <MatterLifecycleBoard
          matters={[matter(), matter({ id: 'matter-2', matter_name: 'Beta filing', status: 'active' })]}
          onMove={vi.fn()}
          onToggleActive={vi.fn()}
          togglingId={null}
          movingId={null}
        />
      </MemoryRouter>,
    )
    MATTER_LIFECYCLE_COLUMNS.forEach(column => {
      expect(screen.getByRole('heading', { name: column.label })).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: 'Acme contract review' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Beta filing' })).toBeInTheDocument()
  })
})

describe('MyMatterRow', () => {
  it('renders the Clio-parity columns and the active-work action', () => {
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <MyMatterRow m={matter()} onToggleActive={vi.fn()} togglingId={null} />
          </tbody>
        </table>
      </MemoryRouter>,
    )
    expect(screen.getByText('ACME0007')).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Kara Bedingfield')).toBeInTheDocument()
    expect(screen.getByText('Richard Mayhew')).toBeInTheDocument()
    expect(screen.getByText('Commercial')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Set Active' })).toBeInTheDocument()
  })

  it('renders only the requested columns', () => {
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <MyMatterRow
              m={matter()}
              columns={['matter', 'client']}
              onToggleActive={vi.fn()}
              togglingId={null}
            />
          </tbody>
        </table>
      </MemoryRouter>,
    )
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.queryByText('Kara Bedingfield')).not.toBeInTheDocument()
    expect(screen.queryByText('Commercial')).not.toBeInTheDocument()
  })

  it('calls onToggleActive with the assignment when active-work is toggled', async () => {
    const user = userEvent.setup()
    const onToggleActive = vi.fn()
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <MyMatterRow m={matter()} onToggleActive={onToggleActive} togglingId={null} />
          </tbody>
        </table>
      </MemoryRouter>,
    )
    await user.click(screen.getByRole('button', { name: 'Set Active' }))
    expect(onToggleActive).toHaveBeenCalledWith('assignment-1', 'matter-1', true)
  })
})

describe('useMatterListColumns', () => {
  function Probe({ user }) {
    const { hidden, save, visibleKeys } = useMatterListColumns(user)
    return (
      <>
        <span data-testid="hidden">{hidden.join(',')}</span>
        <span data-testid="visible">{visibleKeys.join(',')}</span>
        <button type="button" onClick={() => save(['client'])}>Hide client</button>
      </>
    )
  }

  it('hides only cloud folders by default and always keeps matter', async () => {
    localStorage.clear()
    const user = userEvent.setup()
    render(<Probe user={{ tenant_id: 't1', id: 'u1' }} />)
    expect(screen.getByTestId('hidden')).toHaveTextContent(MATTER_LIST_DEFAULT_HIDDEN.join(','))
    expect(screen.getByTestId('visible')).toHaveTextContent('matter')
    expect(screen.getByTestId('visible')).not.toHaveTextContent('cloud_folder')

    await user.click(screen.getByRole('button', { name: 'Hide client' }))
    expect(screen.getByTestId('hidden')).toHaveTextContent('client')
    expect(screen.getByTestId('visible')).not.toHaveTextContent('client')
    expect(MATTER_LIST_COLUMN_DEFS.some(def => def.key === 'matter' && def.locked)).toBe(true)
  })
})
