import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { ConfirmProvider } from './dialog/ConfirmProvider'
import CompliancePanel, { AgreementAcceptancePanel } from './CompliancePanel'

vi.mock('../api', () => ({
  getComplianceAgreements: vi.fn(),
  acceptComplianceAgreement: vi.fn(),
  getRetentionInventory: vi.fn(),
  updateRetentionPolicy: vi.fn(),
  executeRetention: vi.fn(),
}))

const agreementStatus = {
  configured: true,
  complete: false,
  enforced: true,
  blocking: true,
  agreements: [{
    id: 'agreement-1',
    kind: 'master_services_agreement',
    version: '2026-08-27',
    title: 'Master Services Agreement',
    document_url: 'https://legal.example.test/msa.pdf',
    content_hash: 'a'.repeat(64),
    required: true,
    accepted: false,
  }],
}

const retentionInventory = {
  legal_hold: false,
  legal_hold_reason: null,
  policy: { chat_attachments_days: 7 },
  policy_version: 1,
  categories: [{
    name: 'chat_attachments',
    system: 'postgres_and_upload_bind',
    record_count: 2,
    bytes: 2048,
    retention_mode: 'rolling_7_days',
    deletion_supported: true,
  }],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getComplianceAgreements.mockResolvedValue(agreementStatus)
  api.getRetentionInventory.mockResolvedValue(retentionInventory)
  api.updateRetentionPolicy.mockResolvedValue(retentionInventory)
  api.executeRetention.mockResolvedValue({
    eligible_records: 1,
    eligible_bytes: 1024,
    deleted_records: 0,
  })
})

describe('tenant compliance controls', () => {
  it('binds acceptance to the exact agreement the admin reviewed', async () => {
    const user = userEvent.setup()
    api.acceptComplianceAgreement.mockResolvedValue({
      ...agreementStatus,
      complete: true,
      blocking: false,
      agreements: [{
        ...agreementStatus.agreements[0],
        accepted: true,
        accepted_at: '2026-08-27T12:00:00Z',
        signer_name: 'Avery Counsel',
        signer_title: 'Managing Partner',
      }],
    })
    render(<AgreementAcceptancePanel />)

    await user.type(await screen.findByLabelText('Full legal name'), 'Avery Counsel')
    await user.type(screen.getByLabelText('Title / authority'), 'Managing Partner')
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /Accept Master Services Agreement/ }))

    await waitFor(() => expect(api.acceptComplianceAgreement).toHaveBeenCalledWith(
      'master_services_agreement',
      expect.objectContaining({
        expected_version: '2026-08-27',
        expected_content_hash: 'a'.repeat(64),
        signer_name: 'Avery Counsel',
        signer_title: 'Managing Partner',
        authority_attested: true,
      }),
    ))
    expect(await screen.findByText('Accepted')).toBeInTheDocument()
  })

  it('shows metadata inventory and requires a preview before deletion', async () => {
    render(<ConfirmProvider><CompliancePanel /></ConfirmProvider>)
    expect(await screen.findByText('chat attachments')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB · postgres_and_upload_bind')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete previewed attachments' })).toBeDisabled()
  })
})
