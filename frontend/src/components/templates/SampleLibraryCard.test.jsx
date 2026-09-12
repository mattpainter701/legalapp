import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getSampleTemplates, getSampleTemplateSource } from '../../api'
import SampleLibraryCard from './SampleLibraryCard'

vi.mock('../../api', () => ({
  getSampleTemplates: vi.fn(),
  getSampleTemplateSource: vi.fn(),
}))

const samples = [
  {
    id: 'will-1',
    title: 'Last Will and Testament',
    category: 'will',
    jurisdictions: ['North Dakota'],
    field_count: 26,
    variable_schema: { version: 1, fields: [{ name: 'client_name', label: 'Client Name', field_type: 'text' }] },
  },
  {
    id: 'poa-1',
    title: 'Durable Power of Attorney',
    category: 'power_of_attorney',
    jurisdictions: ['Arizona'],
    field_count: 42,
    variable_schema: { version: 1, fields: [] },
  },
  {
    id: 'will-2',
    title: 'Alaska Last Will and Testament',
    category: 'will',
    jurisdictions: ['Alaska'],
    field_count: 57,
    variable_schema: { version: 1, fields: [] },
  },
  {
    id: 'lease-1',
    title: 'Alabama Residential Lease Agreement',
    category: 'lease',
    jurisdictions: ['Alabama'],
    field_count: 42,
    variable_schema: { version: 1, fields: [] },
  },
  {
    id: 'contract-1',
    title: 'Independent Contractor Agreement',
    category: 'contract',
    jurisdictions: [],
    field_count: 18,
    variable_schema: { version: 1, fields: [] },
  },
]

function rowFor(title) {
  return screen.getByText(title).closest('li')
}

describe('SampleLibraryCard', () => {
  beforeEach(() => {
    getSampleTemplates.mockResolvedValue({ items: samples, total: 2 })
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('loads samples grouped by type and ordered by jurisdiction', async () => {
    render(<SampleLibraryCard />)
    await waitFor(() => expect(getSampleTemplates).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('Last Will and Testament')).toBeInTheDocument()
    const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)
    expect(headings).toEqual(['Contract', 'Lease', 'Power of Attorney', 'Will'])
    const willSection = screen.getByRole('heading', { name: 'Will' }).closest('section')
    const willRows = within(willSection).getAllByRole('listitem')
    expect(willRows[0]).toHaveTextContent('Alaska Last Will and Testament')
    expect(willRows[1]).toHaveTextContent('North Dakota · 26 fields')
    expect(screen.getByText('Alabama · 42 fields')).toBeInTheDocument()
    expect(rowFor('Independent Contractor Agreement')).toHaveTextContent('General')
  })

  it('filters the list by category and jurisdiction', async () => {
    render(<SampleLibraryCard />)
    await screen.findByText('Durable Power of Attorney')
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'power_of_attorney' } })
    expect(screen.queryByText('Last Will and Testament')).not.toBeInTheDocument()
    expect(screen.getByText('Durable Power of Attorney')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'all' } })
    fireEvent.change(screen.getByLabelText('Jurisdiction'), { target: { value: 'North Dakota' } })
    expect(screen.getByText('Last Will and Testament')).toBeInTheDocument()
    expect(screen.queryByText('Durable Power of Attorney')).not.toBeInTheDocument()
  })

  it('opens the source PDF in a new tab when previewing', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const createUrl = vi.fn(() => 'blob:preview')
    const revokeUrl = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL: createUrl, revokeObjectURL: revokeUrl })
    getSampleTemplateSource.mockResolvedValue(new Blob(['%PDF-1.4'], { type: 'application/pdf' }))
    render(<SampleLibraryCard />)
    await screen.findByText('Durable Power of Attorney')
    fireEvent.click(within(rowFor('Last Will and Testament')).getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(getSampleTemplateSource).toHaveBeenCalledWith('will-1'))
    await waitFor(() => expect(openSpy).toHaveBeenCalledWith('blob:preview', '_blank', 'noopener'))
    openSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('shows an alert when the preview cannot be opened', async () => {
    getSampleTemplateSource.mockRejectedValue(new Error('offline'))
    render(<SampleLibraryCard />)
    await screen.findByText('Durable Power of Attorney')
    fireEvent.click(within(rowFor('Last Will and Testament')).getByRole('button', { name: 'Preview' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be opened')
  })

  it('opens the fill dialog and closes it again', async () => {
    render(<SampleLibraryCard />)
    await screen.findByText('Durable Power of Attorney')
    fireEvent.click(within(rowFor('Last Will and Testament')).getByRole('button', { name: 'Fill' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Fill “Last Will and Testament”' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows a load error when the catalog fails', async () => {
    getSampleTemplates.mockRejectedValue(new Error('offline'))
    render(<SampleLibraryCard />)
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be loaded')
  })
})
