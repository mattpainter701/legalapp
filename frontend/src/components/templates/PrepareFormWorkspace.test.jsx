import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PrepareFormWorkspace, { canvasToOverlayRect, overlayToCanvasRect } from './PrepareFormWorkspace'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('PrepareFormWorkspace', () => {
  it('converts PDF bottom-left rectangles to canvas top-left coordinates and back', () => {
    const page = { width: 612, height: 792 }
    const canvas = overlayToCanvasRect({ rect: [72, 600, 220, 624] }, page)
    expect(canvas).toEqual({ x: 72, y: 168, width: 148, height: 24 })
    expect(canvasToOverlayRect(canvas, page)).toEqual([72, 600, 220, 624])
  })

  it('creates a manual field and exposes editable properties and inclusion', () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:source')
    const onFieldsChange = vi.fn()
    render(<PrepareFormWorkspace file={new File(['image'], 'form.png', { type: 'image/png' })} analysis={{ suggested_variable_schema: { pages: [{ page: 1, width: 612, height: 792 }] } }} fields={[]} onFieldsChange={onFieldsChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'text', exact: true }))
    const created = onFieldsChange.mock.calls[0][0][0]
    expect(created.name).toMatch(/^field_/)
    expect(created.pdf_source_key).toMatch(/^manual:/)
    expect(created.pdf_overlay.source_kind).toBe('manual')
    expect(created.included).toBe(true)
  })

  it('stores paragraph fields as supported text overlays with multiline behavior', () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:source')
    const onFieldsChange = vi.fn()
    render(<PrepareFormWorkspace file={new File(['image'], 'form.png', { type: 'image/png' })} analysis={{ suggested_variable_schema: { pages: [{ page: 1, width: 612, height: 792 }] } }} fields={[]} onFieldsChange={onFieldsChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'multiline' }))

    expect(onFieldsChange.mock.calls[0][0][0]).toEqual(expect.objectContaining({
      field_type: 'text',
      multiline: true,
    }))
  })

  it('shows every repeated placement on its page and asks for review on OCR-only analysis', () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:source')
    const field = {
      name: 'client_name',
      label: 'Client name',
      pdf_source_key: 'overlay:client-name',
      confidence: 0.7,
      pdf_overlays: [
        { page: 1, rect: [72, 600, 220, 624], source_kind: 'ocr' },
        { page: 2, rect: [80, 500, 240, 524], source_kind: 'ocr' },
      ],
    }
    const onReviewConfirmed = vi.fn()
    render(<PrepareFormWorkspace file={new File(['image'], 'form.png', { type: 'image/png' })} analysis={{ suggested_variable_schema: { detection: { method: 'ocr' }, pages: [{ page: 1, width: 612, height: 792 }, { page: 2, width: 612, height: 792 }] } }} fields={[field]} onFieldsChange={vi.fn()} onReviewConfirmed={onReviewConfirmed} />)

    expect(screen.getByRole('button', { name: 'Select Client name' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByRole('button', { name: 'Select Client name' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Confirm source comparison' }))
    expect(onReviewConfirmed).toHaveBeenCalledWith(true)
  })

  it('updates inclusion through the property inspector', () => {
    const field = { name: 'client_name', label: 'Client name', page: 1, included: true, pdf_overlay: { page: 1, rect: [72, 600, 220, 624], source_kind: 'ocr', erase_source: false }, pdf_overlays: [{ page: 1, rect: [72, 600, 220, 624], source_kind: 'ocr', erase_source: false }], confidence: 0.5, review_required: true }
    const onFieldsChange = vi.fn()
    render(<PrepareFormWorkspace file={new File(['image'], 'form.png', { type: 'image/png' })} analysis={{ suggested_variable_schema: { pages: [{ page: 1, width: 612, height: 792 }] } }} fields={[field]} onFieldsChange={onFieldsChange} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include in template' }))
    expect(onFieldsChange.mock.calls.at(-1)[0][0].included).toBe(false)
  })

  it('keeps the same field selected while its automation key is renamed', async () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:source')
    const onFieldsChange = vi.fn()
    const fields = [
      { name: 'first_name', _bodyName: 'first_name', label: 'First field', pdf_overlay: { page: 1, rect: [40, 680, 180, 706], source_kind: 'text' } },
      { name: 'second_name', _bodyName: 'second_name', label: 'Second field', pdf_overlay: { page: 1, rect: [40, 630, 180, 656], source_kind: 'text' } },
    ]
    const props = {
      file: new File(['image'], 'form.png', { type: 'image/png' }),
      analysis: { suggested_variable_schema: { pages: [{ page: 1, width: 612, height: 792 }] } },
      onFieldsChange,
    }
    const { rerender } = render(<PrepareFormWorkspace {...props} fields={fields} />)
    fireEvent.click(screen.getByRole('button', { name: 'Select Second field' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Automation key' }), { target: { value: 'renamed_field' } })
    const updated = onFieldsChange.mock.calls.at(-1)[0]

    rerender(<PrepareFormWorkspace {...props} fields={updated} />)

    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Automation key' })).toHaveValue('renamed_field'))
    expect(screen.getByRole('textbox', { name: 'Label' })).toHaveValue('Second field')
  })

  it('requires opening the original when the PDF page preview fails', async () => {
    const onReviewConfirmed = vi.fn()
    const onSourceReviewReadyChange = vi.fn()
    render(
      <PrepareFormWorkspace
        file={new File(['not a valid PDF'], 'form.pdf', { type: 'application/pdf' })}
        previewUrl="blob:source"
        analysis={{ suggested_variable_schema: { detection: { method: 'ocr' }, pages: [{ page: 1, width: 612, height: 792 }] } }}
        fields={[]}
        onFieldsChange={vi.fn()}
        onReviewConfirmed={onReviewConfirmed}
        onSourceReviewReadyChange={onSourceReviewReadyChange}
      />,
    )

    const openOriginal = await screen.findByRole('link', { name: 'Open original in a new tab' })
    expect(screen.getByRole('checkbox', { name: 'Confirm source comparison' })).toBeDisabled()
    fireEvent.click(openOriginal)
    expect(screen.getByRole('checkbox', { name: 'Confirm source comparison' })).toBeEnabled()
    expect(onSourceReviewReadyChange).toHaveBeenLastCalledWith(true)
  })
})
