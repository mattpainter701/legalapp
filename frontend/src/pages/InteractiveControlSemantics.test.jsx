import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ConversationItem from '../components/chat/ConversationItem'
import { ResultCard } from './IntakeDashboardPage'
import { PlatformTenantRow } from './PlatformPage'
import { PluginCard } from './PluginsPage'

describe('interactive control semantics', () => {
  afterEach(() => cleanup())

  it('uses the tenant Details button as the sole disclosure control', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(
      <table>
        <tbody>
          <PlatformTenantRow
            tenant={{
              id: 'tenant-1',
              name: 'Northwind Legal',
              domain: 'northwind.example',
              billing_tier: 'flat',
              user_count: 4,
              requests_30d: 200,
              cost_usd_30d: 18,
              is_active: true,
            }}
            expanded={false}
            onToggle={onToggle}
          />
        </tbody>
      </table>,
    )

    const row = screen.getByText('Northwind Legal').closest('tr')
    expect(row).toHaveRole('row')
    expect(row).not.toHaveAttribute('role')
    expect(row).not.toHaveAttribute('tabindex')

    const details = screen.getByRole('button', { name: 'Details' })
    expect(details).toHaveAttribute('aria-expanded', 'false')
    expect(details).toHaveAttribute('aria-controls', 'tenant-details-tenant-1')
    expect(details).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    details.focus()
    await user.keyboard('{Enter}')
    expect(onToggle).toHaveBeenCalledOnce()
    expect(onToggle).toHaveBeenCalledWith('tenant-1')
  })

  it('keeps intake result selection separate from its assignment action', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const onAssign = vi.fn()
    render(
      <ResultCard
        item={{ result_type: 'lead', title: 'Morgan inquiry', lead_id: 'lead-1', score: 95 }}
        selected={false}
        onSelect={onSelect}
        onAssign={onAssign}
      />,
    )

    const card = screen.getByRole('button', { name: 'Select Active lead: Morgan inquiry' })
    const assign = screen.getByRole('button', { name: 'Assign next' })
    expect(card).not.toContainElement(assign)
    expect(assign).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    card.focus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledTimes(1)

    assign.focus()
    await user.keyboard('{Enter}')
    expect(onAssign).toHaveBeenCalledOnce()
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('uses the plugin primary action without making the card interactive', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    const onEntitlement = vi.fn()
    const { container, rerender } = render(
      <PluginCard
        plugin={{
          id: 'commercial-legal',
          display_name: 'Commercial Legal',
          description: 'Commercial workflow',
          category: 'Legal',
        }}
        isAdmin
        saving={null}
        onEntitlement={onEntitlement}
        onNavigate={onNavigate}
      />,
    )

    expect(container.querySelector('[role="link"]')).not.toBeInTheDocument()
    expect(container.querySelector('[tabindex]')).not.toBeInTheDocument()

    const open = screen.getByRole('button', { name: 'View Add-on' })
    expect(open).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    open.focus()
    await user.keyboard('{Enter}')
    expect(onNavigate).toHaveBeenCalledOnce()
    expect(onNavigate).toHaveBeenCalledWith('/plugins/commercial-legal')

    const trial = screen.getByRole('button', { name: 'Trial' })
    const purchase = screen.getByRole('button', { name: 'Purchase' })
    expect(trial).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    expect(purchase).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    trial.focus()
    await user.keyboard('{Enter}')
    expect(onEntitlement).toHaveBeenCalledWith('commercial-legal', 'trial')
    expect(onNavigate).toHaveBeenCalledOnce()

    rerender(
      <PluginCard
        plugin={{
          id: 'commercial-legal',
          display_name: 'Commercial Legal',
          description: 'Commercial workflow',
          category: 'Legal',
          is_purchased: true,
          setup_status: 'complete',
        }}
        isAdmin
        saving={null}
        onEntitlement={onEntitlement}
        onNavigate={onNavigate}
      />,
    )

    for (const name of ['Open Workspace', 'Configure', 'Disable']) {
      expect(screen.getByRole('button', { name })).toHaveClass('min-h-[44px]', 'min-w-[44px]')
    }
  })

  it('keeps conversation selection, pin, and delete as sibling keyboard controls', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const onTogglePin = vi.fn()
    const onDelete = vi.fn()
    render(
      <ConversationItem
        conv={{ id: 'conversation-1', title: 'Client update' }}
        index={0}
        isActive={false}
        isPinned={false}
        onClick={onSelect}
        onTogglePin={onTogglePin}
        onDelete={onDelete}
      />,
    )

    const select = screen.getByRole('button', { name: 'Client update' })
    const pin = screen.getByRole('button', { name: 'Pin Client update' })
    const remove = screen.getByRole('button', { name: 'Delete Client update' })
    expect(select).not.toContainElement(pin)
    expect(select).not.toContainElement(remove)
    for (const control of [select, pin, remove]) {
      expect(control).toHaveClass('min-h-[44px]')
    }
    expect(pin).toHaveClass('min-w-[44px]')
    expect(remove).toHaveClass('min-w-[44px]')
    expect(pin.parentElement).not.toHaveClass('opacity-0')

    await user.tab()
    expect(select).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledOnce()

    await user.tab()
    expect(pin).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onTogglePin).toHaveBeenCalledWith('conversation-1')
    expect(onSelect).toHaveBeenCalledOnce()

    await user.tab()
    expect(remove).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onDelete).toHaveBeenCalledWith('conversation-1')
    expect(onSelect).toHaveBeenCalledOnce()
  })
})
