import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import SkillOutput from './SkillOutput'

describe('SkillOutput', () => {
  afterEach(cleanup)

  it('renders the memo, review banner and run metadata for a completed run', () => {
    render(
      <SkillOutput
        result={{
          memo: '## Key Risks\n\nIndemnity is uncapped.',
          gates_triggered: [],
          requires_attorney_review: true,
          tokens_used: 4210,
          model_used: 'deepseek-chat',
        }}
      />
    )

    expect(screen.getByText('Key Risks')).toBeInTheDocument()
    expect(screen.getByText('Indemnity is uncapped.')).toBeInTheDocument()
    expect(
      screen.getByText(/requires attorney review before any action is taken/i)
    ).toBeInTheDocument()
    expect(screen.getByText('Tokens: 4,210')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /copy memo/i })).toBeInTheDocument()
  })

  it('surfaces blocking gates instead of silently returning an empty memo', () => {
    render(
      <SkillOutput
        result={{
          memo: 'GATE: No practice profile found.',
          gates_triggered: ['GATE: No practice profile found for commercial-legal.'],
          requires_attorney_review: true,
        }}
      />
    )

    expect(
      screen.getByText('GATE: No practice profile found for commercial-legal.')
    ).toBeInTheDocument()
  })

  // Advertised skills with no curated template used to fall through to a
  // generic prompt with no signal to the user.
  it('warns when a run used the generic template', () => {
    render(
      <SkillOutput
        result={{
          memo: 'General guidance.',
          flags: ['This workflow has no specialised template yet.'],
          requires_attorney_review: true,
        }}
      />
    )

    expect(
      screen.getByText('This workflow has no specialised template yet.')
    ).toBeInTheDocument()
  })

  it('renders nothing when there is no result yet', () => {
    const { container } = render(<SkillOutput result={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
