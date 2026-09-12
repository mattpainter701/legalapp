import { act, cleanup, render, renderHook, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MyMattersTable } from './MatterPortfolioPage'
import {
  MATTER_LIST_COLUMN_BY_KEY,
  nextSortState,
  sortMatters,
  useMatterListColumns,
} from '../components/matters/MatterListColumns'

const matter = (overrides = {}) => ({
  id: 'matter-1',
  matter_name: 'Acme contract review',
  matter_number: 'ACME0007',
  client_name: 'Acme Corp',
  status: 'open',
  risk_level: 'low',
  created_at: '2026-01-15T00:00:00Z',
  ...overrides,
})

afterEach(cleanup)

describe('matter list ordering', () => {
  it('orders text columns both ways and keeps blanks last in each', () => {
    const rows = [
      matter({ id: 'a', client_name: 'Zenith Holdings' }),
      matter({ id: 'b', client_name: null }),
      matter({ id: 'c', client_name: 'Acme Corp' }),
    ]

    expect(sortMatters(rows, 'client', 'asc').map(m => m.id)).toEqual(['c', 'a', 'b'])
    // The unnamed client is not "after Z" — it has no value, so it stays last.
    expect(sortMatters(rows, 'client', 'desc').map(m => m.id)).toEqual(['a', 'c', 'b'])
  })

  it('orders the next deadline by date, not by the badge text', () => {
    const rows = [
      matter({ id: 'later', next_deadline: '2026-12-01T00:00:00Z', overdue_deadline_label: null }),
      matter({ id: 'none', next_deadline: null }),
      matter({ id: 'overdue', next_deadline: '2026-08-13T00:00:00Z', overdue_deadline_label: '31 days overdue' }),
    ]

    expect(sortMatters(rows, 'deadline', 'asc').map(m => m.id)).toEqual(['overdue', 'later', 'none'])
  })

  it('orders risk by severity so descending means worst first', () => {
    const rows = [
      matter({ id: 'medium', risk_level: 'medium' }),
      matter({ id: 'critical', risk_level: 'critical' }),
      matter({ id: 'unset', risk_level: null }),
      matter({ id: 'low', risk_level: 'low' }),
    ]

    expect(sortMatters(rows, 'risk', 'desc').map(m => m.id)).toEqual([
      'critical', 'medium', 'low', 'unset',
    ])
  })

  it('orders status by lifecycle so live work sits above closed work', () => {
    const rows = [
      matter({ id: 'closed', status: 'closed' }),
      matter({ id: 'active', status: 'active' }),
      matter({ id: 'open', status: 'open' }),
    ]

    expect(sortMatters(rows, 'status', 'asc').map(m => m.id)).toEqual(['open', 'active', 'closed'])
  })

  it('keeps equal rows in the order the server sent them', () => {
    const rows = [
      matter({ id: 'first', practice_area: 'Family' }),
      matter({ id: 'second', practice_area: 'Family' }),
      matter({ id: 'third', practice_area: 'Family' }),
    ]

    expect(sortMatters(rows, 'practice_area', 'asc').map(m => m.id)).toEqual([
      'first', 'second', 'third',
    ])
  })

  it('leaves the list untouched when no column is chosen', () => {
    const rows = [matter({ id: 'b' }), matter({ id: 'a' })]
    expect(sortMatters(rows, null, null)).toBe(rows)
  })

  it('cycles a header through its natural direction, the opposite, then off', () => {
    let state = { key: null, direction: null }
    state = nextSortState(state, 'client')
    expect(state).toEqual({ key: 'client', direction: 'asc' })
    state = nextSortState(state, 'client')
    expect(state).toEqual({ key: 'client', direction: 'desc' })
    state = nextSortState(state, 'client')
    expect(state).toEqual({ key: null, direction: null })
  })

  it('starts open date and risk on the direction a reader wants first', () => {
    expect(nextSortState({ key: null, direction: null }, 'open_date').direction).toBe('desc')
    expect(nextSortState({ key: null, direction: null }, 'risk').direction).toBe('desc')
  })

  it('ignores a column that has nothing to compare', () => {
    const state = { key: 'client', direction: 'asc' }
    expect(nextSortState(state, 'cloud_folder')).toBe(state)
  })
})

describe('matter list column widths', () => {
  beforeEach(() => localStorage.clear())

  const user = { id: 'user-1', tenant_id: 'tenant-1' }

  it('remembers a resized column and clamps it to the column floor', () => {
    const { result } = renderHook(() => useMatterListColumns(user))
    const floor = MATTER_LIST_COLUMN_BY_KEY.client.minWidth

    act(() => result.current.setWidth('client', 320))
    expect(result.current.widthOf('client')).toBe(320)

    act(() => result.current.setWidth('client', 10))
    expect(result.current.widthOf('client')).toBe(floor)

    const reloaded = renderHook(() => useMatterListColumns(user))
    expect(reloaded.result.current.widthOf('client')).toBe(floor)
  })

  it('puts one column, or every column, back to its default width', () => {
    const { result } = renderHook(() => useMatterListColumns(user))
    const defaultWidth = MATTER_LIST_COLUMN_BY_KEY.client.width

    act(() => result.current.setWidth('client', 300))
    act(() => result.current.setWidth('status', 200))
    expect(result.current.resized).toBe(true)

    act(() => result.current.resetWidth('client'))
    expect(result.current.widthOf('client')).toBe(defaultWidth)
    expect(result.current.widthOf('status')).toBe(200)

    act(() => result.current.resetWidth())
    expect(result.current.resized).toBe(false)
    expect(result.current.widthOf('status')).toBe(MATTER_LIST_COLUMN_BY_KEY.status.width)
  })
})

describe('my matters table', () => {
  const columns = (overrides = {}) => ({
    visibleKeys: ['matter', 'client', 'open_date', 'cloud_folder'],
    widthOf: key => MATTER_LIST_COLUMN_BY_KEY[key].width,
    setWidth: vi.fn(),
    resetWidth: vi.fn(),
    ...overrides,
  })

  const table = (props = {}) => {
    const merged = {
      matters: [matter()],
      columns: columns(),
      sort: { key: null, direction: null },
      onSort: vi.fn(),
      onToggleActive: vi.fn(),
      togglingId: null,
      ...props,
    }
    render(
      <MemoryRouter>
        <MyMattersTable {...merged} />
      </MemoryRouter>,
    )
    return merged
  }

  it('offers every value column as a sort control and reports the click', async () => {
    const user = userEvent.setup()
    const props = table()

    await user.click(screen.getByRole('button', { name: /Client/ }))
    expect(props.onSort).toHaveBeenCalledWith('client')

    await user.click(screen.getByRole('button', { name: /Open date/ }))
    expect(props.onSort).toHaveBeenCalledWith('open_date')
  })

  it('announces which column is sorted and which way', () => {
    table({ sort: { key: 'open_date', direction: 'desc' } })

    const sorted = screen.getByRole('columnheader', { name: /Open date/ })
    expect(sorted).toHaveAttribute('aria-sort', 'descending')
    expect(screen.getByRole('columnheader', { name: /Client/ })).toHaveAttribute('aria-sort', 'none')
  })

  it('leaves a column with nothing to compare as a plain label', () => {
    table()

    const header = screen.getByRole('columnheader', { name: /Cloud folder/ })
    expect(within(header).queryByRole('button')).not.toBeInTheDocument()
    expect(header).not.toHaveAttribute('aria-sort')
  })

  it('sizes each column from the width store rather than from its content', () => {
    const { container } = render(
      <MemoryRouter>
        <MyMattersTable
          matters={[matter()]}
          columns={columns({ widthOf: key => (key === 'client' ? 333 : 120) })}
          sort={{ key: null, direction: null }}
          onSort={vi.fn()}
          onToggleActive={vi.fn()}
          togglingId={null}
        />
      </MemoryRouter>,
    )

    const cols = container.querySelectorAll('colgroup col')
    expect(cols[1]).toHaveStyle({ width: '333px' })
    expect(container.querySelector('table')).toHaveClass('table-fixed')
  })

  it('resizes a column from the keyboard, and resets it on Enter', async () => {
    const user = userEvent.setup()
    const props = table()

    const handle = screen.getByRole('separator', { name: 'Resize Client column' })
    handle.focus()
    await user.keyboard('{ArrowRight}')
    const [key, widened] = props.columns.setWidth.mock.calls.at(-1)
    expect(key).toBe('client')
    expect(widened).toBeGreaterThan(MATTER_LIST_COLUMN_BY_KEY.client.width)

    await user.keyboard('{ArrowLeft}')
    expect(props.columns.setWidth.mock.calls.at(-1)[1]).toBeLessThan(
      MATTER_LIST_COLUMN_BY_KEY.client.width,
    )

    await user.keyboard('{Enter}')
    expect(props.columns.resetWidth).toHaveBeenCalledWith('client')
  })

  it('drags a column wider by the distance the pointer travelled', () => {
    const props = table()
    const handle = screen.getByRole('separator', { name: 'Resize Client column' })
    const startWidth = MATTER_LIST_COLUMN_BY_KEY.client.width

    act(() => {
      handle.dispatchEvent(
        new MouseEvent('pointerdown', { bubbles: true, clientX: 400, button: 0 }),
      )
      handle.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 460 }))
    })

    expect(props.columns.setWidth).toHaveBeenCalledWith('client', startWidth + 60)
  })
})
