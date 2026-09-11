import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TemplateCardRail, { fieldPath, instanceChoices } from './TemplateCardRail'
import { cardHue } from './cardColor'

const field = (key, label, extra = {}) => ({
  key, label, path: `x.${key}`, value_kind: 'text', suggested_name: key, supports_all_instances: false, ...extra,
})

const matterCard = {
  key: 'matter', label: 'Matter', kind: 'matter', group: 'Matter', max_instances: 1, instance_count: null,
  fields: [field('case_number', 'Case number'), field('court', 'Court')],
}

const defendantCard = (instanceCount) => ({
  key: 'defendant', label: 'Defendant', kind: 'role', group: 'Parties', max_instances: 20,
  instance_count: instanceCount,
  fields: [field('full_name', 'Full name', { supports_all_instances: true }), field('email', 'Email')],
})

afterEach(cleanup)

describe('fieldPath', () => {
  it('omits the instance for a singleton and for the first instance', () => {
    // The two spellings must produce one path, or a template could bind the
    // same record twice under names that drift apart.
    expect(fieldPath('matter', null, 'court')).toBe('matter.court')
    expect(fieldPath('defendant', 1, 'full_name')).toBe('defendant.full_name')
  })

  it('addresses later instances and every instance', () => {
    expect(fieldPath('defendant', 2, 'full_name')).toBe('defendant.2.full_name')
    expect(fieldPath('defendant', '*', 'full_name')).toBe('defendant.*.full_name')
  })
})

describe('instanceChoices', () => {
  it('offers only the first instance when no matter is loaded', () => {
    // instance_count null means "not known", which is not "none" — offering
    // Defendant 2 here would invite a binding that may never fill.
    expect(instanceChoices(defendantCard(null))).toEqual([1])
  })

  it('offers one choice per party on the matter', () => {
    expect(instanceChoices(defendantCard(3))).toEqual([1, 2, 3])
  })

  it('never offers fewer than one or more than the ceiling', () => {
    expect(instanceChoices(defendantCard(0))).toEqual([1])
    expect(instanceChoices({ ...defendantCard(50), max_instances: 2 })).toEqual([1, 2])
  })

  it('gives a singleton card no instances', () => {
    expect(instanceChoices(matterCard)).toEqual([null])
  })
})

describe('cardHue', () => {
  it('is stable for a given card', () => {
    expect(cardHue('defendant')).toBe(cardHue('defendant'))
  })

  it('does not collide a new card with a named one', () => {
    const named = ['firm', 'matter', 'billing', 'client', 'plaintiff', 'defendant', 'attorney', 'preparer', 'item']
    const reserved = new Set(named.map(cardHue))
    for (const key of ['trustee', 'witness', 'executor', 'mediator', 'guardian']) {
      expect(reserved.has(cardHue(key))).toBe(false)
    }
  })
})

describe('TemplateCardRail', () => {
  it('groups fields under the subject they belong to', async () => {
    render(<TemplateCardRail cards={[matterCard, defendantCard(2)]} onSelectField={vi.fn()} />)
    // Cards are collapsed until opened, so the rail stays readable with many.
    expect(screen.queryByRole('button', { name: 'Case number' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Matter/ }))
    expect(screen.getByRole('button', { name: 'Case number' })).toBeTruthy()
  })

  it('emits the full card path for the selected instance', async () => {
    const onSelectField = vi.fn()
    render(<TemplateCardRail cards={[defendantCard(2)]} onSelectField={onSelectField} />)
    await userEvent.click(screen.getByRole('button', { name: /Defendant/ }))
    await userEvent.selectOptions(screen.getByLabelText('Defendant instance'), '2')
    await userEvent.click(screen.getByRole('button', { name: 'Full name' }))
    expect(onSelectField.mock.calls[0][0].path).toBe('defendant.2.full_name')
  })

  it('reports how many instances the matter has', async () => {
    render(<TemplateCardRail cards={[defendantCard(2)]} onSelectField={vi.fn()} />)
    expect(screen.getByText('2 on this matter')).toBeTruthy()
  })

  it('says nothing about instance counts when no matter is loaded', () => {
    render(<TemplateCardRail cards={[defendantCard(null)]} onSelectField={vi.fn()} />)
    expect(screen.queryByText(/on this matter/)).toBeNull()
  })

  it('offers under "all instances" only the fields that can be joined', async () => {
    render(<TemplateCardRail cards={[defendantCard(2)]} onSelectField={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Defendant/ }))
    expect(screen.getByRole('button', { name: 'Email' })).toBeTruthy()
    await userEvent.selectOptions(screen.getByLabelText('Defendant instance'), '*')
    // Email has no plural form; offering it would promise a join that cannot
    // happen.
    expect(screen.queryByRole('button', { name: 'Email' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Full name' })).toBeTruthy()
  })

  it('explains an empty catalogue instead of rendering nothing', () => {
    render(<TemplateCardRail cards={[]} onSelectField={vi.fn()} />)
    expect(screen.getByText(/No data sources are available/)).toBeTruthy()
  })
})
