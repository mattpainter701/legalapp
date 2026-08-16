import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, test } from 'vitest'
import VirtualAssistantMockup from './VirtualAssistantMockup'

afterEach(() => cleanup())

describe('VirtualAssistantMockup', () => {
  test('offers the three mobile-first example flows without a backend', () => {
    render(<VirtualAssistantMockup />)

    expect(screen.getByRole('heading', { name: /tell lawhand what to handle/i })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /new client \+ task/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: /log matter time/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: /prepare document packet/i }).length).toBeGreaterThan(0)
  })

  test('has no detectable accessibility violations in the starting state', async () => {
    const { container } = render(<VirtualAssistantMockup />)
    expect(await axe(container)).toHaveNoViolations()
  })

  test('requires a dedicated review action and leaves a mock receipt', async () => {
    const user = userEvent.setup()
    render(<VirtualAssistantMockup />)

    await user.click(screen.getAllByRole('button', { name: /new client \+ task/i })[0])
    const reviewButton = await screen.findByRole('button', { name: /review 2 changes/i })
    await user.click(reviewButton)

    expect(screen.getByRole('dialog', { name: /review 2 changes/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /create contact & task/i }))

    expect((await screen.findAllByText('Client and task created', {}, { timeout: 2500 })).length).toBeGreaterThan(0)
    expect(screen.getByText(/no real records were written/i)).toBeInTheDocument()
  })

  test('blocks time review until the exact matter and billing status are resolved', async () => {
    const user = userEvent.setup()
    render(<VirtualAssistantMockup />)

    await user.click(screen.getAllByRole('button', { name: /log matter time/i })[0])
    const matterSelect = await screen.findByLabelText(/exact matter/i)
    const reviewButton = screen.getByRole('button', { name: /review 3\.00 hours/i })
    expect(reviewButton).toBeDisabled()

    await user.selectOptions(matterSelect, 'ramirez')
    await user.click(screen.getByRole('button', { name: /^billable$/i }))

    expect(reviewButton).toBeEnabled()
  })

  test('scopes each requested document before opening exact review', async () => {
    const user = userEvent.setup()
    render(<VirtualAssistantMockup />)

    await user.click(screen.getAllByRole('button', { name: /prepare document packet/i })[0])
    const matterSelect = await screen.findByLabelText(/matter for all drafts/i)
    const reviewButton = screen.getByRole('button', { name: /review 3 drafts/i })
    expect(reviewButton).toBeDisabled()

    await user.selectOptions(matterSelect, 'acme')
    await user.click(screen.getByRole('button', { name: /^complaint$/i }))
    await user.selectOptions(screen.getByLabelText(/agreement type/i), 'Services agreement')

    expect(reviewButton).toBeEnabled()
    await user.click(reviewButton)
    expect(screen.getByRole('dialog', { name: /review 3 private drafts/i })).toHaveTextContent(/nothing will be filed, finalized, saved to the matter, published, sent, or signed/i)
  })
})
