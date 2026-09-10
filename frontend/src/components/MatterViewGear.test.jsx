import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import MatterViewGear, { useMatterView } from './MatterViewGear'
function Example({ user }) { const view = useMatterView(user); return <><MatterViewGear hidden={view.hidden} onChange={view.save} />{!view.hidden.includes('Court') && <p>Court value</p>}</> }
afterEach(() => { cleanup(); localStorage.clear() })
describe('personal matter fields', () => {
  it('hides a field, persists the choice, and resets it without affecting another user', () => {
    const user = { id: 'one', tenant_id: 'firm' }
    const first = render(<Example user={user} />)
    fireEvent.click(screen.getByText('Customize view')); fireEvent.click(screen.getByLabelText('Court'))
    expect(screen.queryByText('Court value')).toBeNull(); first.unmount()
    const next = render(<Example user={user} />); expect(screen.queryByText('Court value')).toBeNull()
    fireEvent.click(screen.getByText('Customize view')); fireEvent.click(screen.getByText('Reset to default')); expect(screen.getByText('Court value')).toBeVisible(); next.unmount()
    render(<Example user={{ id: 'two', tenant_id: 'firm' }} />); expect(screen.getByText('Court value')).toBeVisible()
  })
})
