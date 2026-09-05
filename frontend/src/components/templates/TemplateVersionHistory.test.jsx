import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TemplateVersionHistory, { describeChange } from './TemplateVersionHistory'
import { listTemplateVersions, restoreTemplateVersion } from '../../api'

vi.mock('../../api', () => ({
  listTemplateVersions: vi.fn(),
  restoreTemplateVersion: vi.fn(),
}))

const version = (overrides = {}) => ({
  version_no: 1,
  title: 'Engagement letter',
  body_sha256: 'b1',
  source_sha256: null,
  source_filename: null,
  is_active: false,
  field_count: 2,
  change_summary: null,
  created_at: '2026-08-01T10:00:00Z',
  ...overrides,
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('describeChange', () => {
  it('prefers the author’s own summary', () => {
    expect(describeChange(version({ change_summary: 'Fixed the fee clause' }), version()))
      .toBe('Fixed the fee clause')
  })

  it('labels the earliest recorded state', () => {
    expect(describeChange(version(), undefined)).toBe('First recorded version')
  })

  it('names what actually changed against the previous version', () => {
    const previous = version({ version_no: 1, field_count: 2 })
    const current = version({
      version_no: 2,
      title: 'Renamed letter',
      body_sha256: 'b2',
      field_count: 4,
      is_active: true,
    })
    const described = describeChange(current, previous)
    expect(described).toContain('renamed')
    expect(described).toContain('content edited')
    expect(described).toContain('fields 2 → 4')
    expect(described).toContain('activated')
  })

  it('reports a source replacement', () => {
    expect(describeChange(version({ source_sha256: 'new' }), version({ source_sha256: 'old' })))
      .toContain('source replaced')
  })

  it('does not invent a change when nothing tracked differs', () => {
    expect(describeChange(version({ version_no: 2 }), version())).toBe('No tracked change')
  })
})

describe('TemplateVersionHistory', () => {
  it('lists recorded versions newest first with a restore control', async () => {
    listTemplateVersions.mockResolvedValue({
      current_version_no: 2,
      total: 2,
      versions: [version({ version_no: 2, body_sha256: 'b2' }), version()],
    })
    render(<TemplateVersionHistory templateId="t1" />)

    expect(await screen.findByText(/Version 2 · Engagement letter/)).toBeInTheDocument()
    expect(screen.getByText(/2 recorded versions/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /restore/i })).toHaveLength(2)
  })

  it('explains an empty history instead of showing a bare list', async () => {
    listTemplateVersions.mockResolvedValue({ current_version_no: 0, total: 0, versions: [] })
    render(<TemplateVersionHistory templateId="t1" />)

    expect(await screen.findByRole('heading', { name: /no history yet/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /restore/i })).not.toBeInTheDocument()
  })

  it('surfaces a load failure rather than an empty history', async () => {
    // Claiming "no history" when the request failed would tell a firm its
    // record is gone.
    listTemplateVersions.mockRejectedValue(new Error('offline'))
    render(<TemplateVersionHistory templateId="t1" />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/i)
  })

  it('hides restore controls on the activity view', async () => {
    listTemplateVersions.mockResolvedValue({ current_version_no: 1, total: 1, versions: [version()] })
    render(<TemplateVersionHistory templateId="t1" mode="activity" />)

    expect(await screen.findByText(/Version 1 · Engagement letter/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /restore/i })).not.toBeInTheDocument()
  })

  it('reports the restored template and reloads the history', async () => {
    listTemplateVersions.mockResolvedValue({ current_version_no: 1, total: 1, versions: [version()] })
    restoreTemplateVersion.mockResolvedValue({ id: 't1', title: 'Engagement letter' })
    const onRestored = vi.fn()
    render(<TemplateVersionHistory templateId="t1" onRestored={onRestored} />)

    fireEvent.click(await screen.findByRole('button', { name: /restore/i }))
    await waitFor(() => expect(onRestored).toHaveBeenCalledWith({ id: 't1', title: 'Engagement letter' }))
    expect(restoreTemplateVersion).toHaveBeenCalledWith('t1', 1)
    await waitFor(() => expect(listTemplateVersions).toHaveBeenCalledTimes(2))
  })

  it('shows the server’s reason when a restore is refused', async () => {
    listTemplateVersions.mockResolvedValue({ current_version_no: 1, total: 1, versions: [version()] })
    restoreTemplateVersion.mockRejectedValue({
      response: { data: { detail: 'That version was recorded against a different source file.' } },
    })
    render(<TemplateVersionHistory templateId="t1" />)

    fireEvent.click(await screen.findByRole('button', { name: /restore/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/different source file/i)
  })
})
