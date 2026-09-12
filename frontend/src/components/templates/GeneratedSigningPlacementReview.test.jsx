import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import GeneratedSigningPlacementReview from './GeneratedSigningPlacementReview'

const state = vi.hoisted(() => ({ rotation: 0, width: 612, height: 792 }))
vi.mock('./PdfDocumentCanvas', async () => {
  const { useEffect } = await import('react')
  const viewport = { viewBox: [0, 0, 612, 792], width: 550.8, height: 712.8,
    convertToViewportRectangle: ([l, b, r, t]) => [l * .9, (792 - b) * .9, r * .9, (792 - t) * .9],
    convertToPdfPoint: (x, y) => [x / .9, 792 - y / .9],
  }
  return {
    useTemplatePdfDocument: () => ({ document: {}, pages: [{ page: 1, width: state.width, height: state.height, rotation: state.rotation }], error: '' }),
    PdfPageCanvas: ({ onViewport }) => { useEffect(() => { onViewport(viewport) }, [onViewport]); return <div>Final PDF</div> },
  }
})
vi.mock('react-rnd', () => ({ Rnd: ({ children, onDragStop, position }) => <div>{children}<button type="button" onClick={() => onDragStop(null, { x: position.x, y: position.y + 90 })}>Move down</button></div> }))

beforeEach(() => {
  state.rotation = 0
  state.width = 612
  state.height = 792
  vi.stubGlobal('crypto', { subtle: { digest: vi.fn(async () => new Uint8Array(32).fill(10).buffer) }, randomUUID: () => 'field-id' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const source = { arrayBuffer: async () => new ArrayBuffer(8) }

describe('GeneratedSigningPlacementReview', () => {
  it('authors role-bound fields against the verified final PDF and moves vertically', async () => {
    const onChange = vi.fn()
    render(<GeneratedSigningPlacementReview source={source} signerRoles={['client', 'attorney']} onChange={onChange} />)
    await screen.findByText(/The final PDF is verified/)
    expect(screen.getByRole('button', { name: 'Add signature for client' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add signature for attorney' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add initials for client' }))
    expect(onChange.mock.lastCall[0][0]).toMatchObject({ role: 'client', field_type: 'initials', page: 1, rect: [72, 100, 192, 124], source_sha256: '0a'.repeat(32) })
    fireEvent.click(screen.getByRole('button', { name: 'Move down' }))
    expect(onChange.mock.lastCall[0][0].rect).toEqual([72, 0, 192, 24])
    fireEvent.click(screen.getByRole('button', { name: 'Delete field-id' }))
    expect(onChange.mock.lastCall[0]).toEqual([])
  })

  it('discards obsolete source placements and refuses unsupported rotation', async () => {
    const onChange = vi.fn()
    const initial = [{ field_id: 'stale', source_sha256: 'old', page: 1 }]
    state.rotation = 90
    render(<GeneratedSigningPlacementReview source={source} signerRoles={['client']} initialFields={initial} onChange={onChange} />)
    await screen.findByText(/The final PDF is verified/)
    expect(screen.getByRole('alert')).toHaveTextContent('Only unrotated PDF pages')
    fireEvent.click(screen.getByRole('button', { name: 'Add signature for client' }))
    expect(onChange.mock.lastCall[0]).toEqual([])
  })

  it('places fields on a page that is not US Letter', async () => {
    const onChange = vi.fn()
    state.width = 595.3
    state.height = 841.9
    render(<GeneratedSigningPlacementReview source={source} signerRoles={['client']} onChange={onChange} />)
    await screen.findByText(/The final PDF is verified/)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add signature for client' }))
    expect(onChange.mock.lastCall[0][0]).toMatchObject({ role: 'client', field_type: 'signature', page_width: 595.3, page_height: 841.9 })
  })

  it('reports a failed digest instead of leaving stale actionable fields', async () => {
    const onChange = vi.fn()
    render(<GeneratedSigningPlacementReview source={{ arrayBuffer: async () => { throw new Error('failed') } }} onChange={onChange} />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('could not be verified'))
    expect(onChange.mock.lastCall[0]).toEqual([])
  })
})
