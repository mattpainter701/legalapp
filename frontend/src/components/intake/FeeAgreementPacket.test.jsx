import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import FeeAgreementPacket from './FeeAgreementPacket'
import { getTemplates } from '../../api'

vi.mock('../../api', () => ({ getTemplates: vi.fn() }))

const TEMPLATE_ID = '10000000-0000-4000-8000-000000000001'

describe('FeeAgreementPacket', () => {
  it('collects firm decisions and never presents a send action', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    getTemplates.mockResolvedValue({
      items: [{
        id: TEMPLATE_ID,
        title: 'Limited-scope engagement',
        category: 'engagement_letter',
        status: 'approved',
        is_active: true,
      }],
    })
    render(<FeeAgreementPacket lead={{ contact: { display_name: 'Alex Client' }, assigned_to_name: 'Dana Lawyer' }} onSave={onSave} />)

    expect(screen.getByLabelText('Client name')).toHaveValue('Alex Client')
    expect(screen.getByLabelText('Attorney name')).toHaveValue('Dana Lawyer')
    await waitFor(() => expect(screen.getByLabelText('Agreement template')).toHaveValue(TEMPLATE_ID))
    await user.type(screen.getByLabelText('Approved fee amount'), '2500')
    await user.type(screen.getByLabelText('Scope of representation'), 'Prepare and file the petition.')
    await user.type(screen.getByLabelText('Client email'), 'alex@example.com')
    await user.type(screen.getByLabelText('Signer email'), 'alex@example.com')
    await user.click(screen.getByRole('button', { name: /save & render preview/i }))

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      template_id: TEMPLATE_ID,
      fee_amount: '2500',
      scope_bullets: ['Prepare and file the petition.'],
      client: { name: 'Alex Client', email: 'alex@example.com' },
    }))
    expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument()
  })
})
