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
vi.mock('react-rnd', () => ({ Rnd: ({ children, onDragStop, position }) => <div>{children}<button type="button" onClick={() => onDragStop(null, { x: position.x, y: position.y + 45 })}>Move down</button></div> }))

let uuidCount = 0
beforeEach(() => {
  state.rotation = 0
  state.width = 612
  state.height = 792
  uuidCount = 0
  vi.stubGlobal('crypto', {
    subtle: { digest: vi.fn(async () => new Uint8Array(32).fill(10).buffer) },
    randomUUID: () => { uuidCount += 1; return `field-id${uuidCount > 1 ? `-${uuidCount}` : ''}` },
  })
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
    // A block a signer can actually read, not the 120x24 strip we used to drop.
    expect(onChange.mock.lastCall[0][0]).toMatchObject({ role: 'client', field_type: 'initials', page: 1, rect: [54, 95.04, 150, 151.04], source_sha256: '0a'.repeat(32) })
    fireEvent.click(screen.getByRole('button', { name: 'Move down' }))
    expect(onChange.mock.lastCall[0][0].rect).toEqual([54, 45.04, 150, 101.04])
    fireEvent.click(screen.getByRole('button', { name: 'Remove Client Initials field on page 1' }))
    expect(onChange.mock.lastCall[0]).toEqual([])
  })

  it('undoes and redoes placement, and deletes the selected field with the keyboard', async () => {
    const onChange = vi.fn()
    render(<GeneratedSigningPlacementReview source={source} signerRoles={['client']} onChange={onChange} />)
    await screen.findByText(/The final PDF is verified/)
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Add signature for client' }))
    fireEvent.click(screen.getByRole('button', { name: 'Move down' }))
    expect(onChange.mock.lastCall[0][0].rect).toEqual([54, 45.04, 284, 101.04])

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(onChange.mock.lastCall[0][0].rect).toEqual([54, 95.04, 284, 151.04])
    fireEvent.click(screen.getByRole('button', { name: 'Redo' }))
    expect(onChange.mock.lastCall[0][0].rect).toEqual([54, 45.04, 284, 101.04])
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(onChange.mock.lastCall[0]).toEqual([])

    // Redo brings the field back, and Delete removes the selected one again.
    fireEvent.click(screen.getByRole('button', { name: 'Redo' }))
    expect(onChange.mock.lastCall[0]).toHaveLength(1)
    fireEvent.keyDown(globalThis.document, { key: 'Delete' })
    expect(onChange.mock.lastCall[0]).toEqual([])
  })

  it('keeps each signer separate: own colour row, own fields, no stacking', async () => {
    const onChange = vi.fn()
    render(<GeneratedSigningPlacementReview
      source={source}
      signerRoles={[{ role: 'client', name: 'Dana Cole' }, { role: 'co_client', name: 'Ravi Shah' }]}
      onChange={onChange}
    />)
    await screen.findByText(/The final PDF is verified/)
    expect(screen.getByText('Dana Cole · Client')).toBeInTheDocument()
    expect(screen.getByText('Ravi Shah · Co Client')).toBeInTheDocument()
    expect(screen.getByText(/No fields placed yet for Dana Cole · Client, Ravi Shah · Co Client/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Add signature for client' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add signature for co_client' }))
    const [first, second] = onChange.mock.lastCall[0]
    expect(first.role).toBe('client')
    expect(second.role).toBe('co_client')
    // The second signer's block lands in its own slot, clear of the first.
    const overlaps = first.rect[0] < second.rect[2] && second.rect[0] < first.rect[2]
      && first.rect[1] < second.rect[3] && second.rect[1] < first.rect[3]
    expect(overlaps).toBe(false)
    expect(screen.getByRole('button', { name: 'Remove Ravi Shah · Co Client Signature field on page 1' })).toBeInTheDocument()
    expect(screen.queryByText(/No fields placed yet/)).not.toBeInTheDocument()
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
