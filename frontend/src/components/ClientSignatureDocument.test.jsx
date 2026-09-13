import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api, { getClientPortalSignatureFields, signClientPortalSignature, uploadClientPortalSignedCopy } from '../api'
import ClientSignatureDocument, { initialsFor } from './ClientSignatureDocument'
import { useTemplatePdfDocument } from './templates/PdfDocumentCanvas'
import { FILING_PENDING_MESSAGE, SIGNED_COPY_RECEIVED_MESSAGE, SIGNED_MESSAGE } from './portal/signingMessages'

vi.mock('../api', () => ({
  default: { get: vi.fn() },
  downloadClientPortalDocumentUrl: (id) => `/download/${id}`,
  getClientPortalSignatureFields: vi.fn(),
  signClientPortalSignature: vi.fn(),
  uploadClientPortalSignedCopy: vi.fn(),
}))

// pdf.js is replaced by a page stub that reports a viewport whose rectangle
// conversion is the plain US-Letter transform at the requested zoom, so overlay
// positions can be asserted in CSS pixels.
vi.mock('./templates/PdfDocumentCanvas', async () => {
  const { useEffect } = await import('react')
  return {
    useTemplatePdfDocument: vi.fn(),
    PdfPageCanvas: ({ pageNumber, zoom, onViewport }) => {
      useEffect(() => {
        onViewport({
          width: 612 * zoom,
          height: 792 * zoom,
          convertToViewportRectangle: ([l, b, r, t]) => [l * zoom, (792 - b) * zoom, r * zoom, (792 - t) * zoom],
        })
      }, [onViewport, zoom])
      return <div aria-label={`Rendered page ${pageNumber}`}>{pageNumber} at {zoom}</div>
    },
  }
})

const pages = [{ page: 1, width: 612, height: 792, rotation: 0 }, { page: 2, width: 612, height: 792, rotation: 0 }]
const request = () => ({ id: 'req-1', document_id: 'fee', document_name: 'Fee agreement', status: 'sent', signers: [{ id: 'signer-1', status: 'pending' }] })
const manifest = () => ({
  request_id: 'req-1', document_id: 'fee', signer_id: 'signer-1', signer_role: 'client', fill_supported: true,
  pages: [{ page: 1, width: 612, height: 792 }, { page: 2, width: 612, height: 792 }],
  fields: [
    { field_id: 'acroform:client_name', kind: 'text', label: 'Client name', required: true, page: 1, rect: [72, 600, 300, 620], mine: true, value: '' },
    { field_id: 'acroform:notes', kind: 'text', label: 'Notes', required: false, multiline: true, page: 1, rect: [72, 500, 300, 580], mine: true, value: '' },
    { field_id: 'acroform:agree', kind: 'checkbox', label: 'I agree', required: true, page: 1, rect: [72, 450, 90, 468], mine: true, value: 'false' },
    { field_id: 'acroform:state', kind: 'choice', label: 'State', required: true, options: ['IL', 'WI'], page: 1, rect: [72, 400, 200, 420], mine: true, value: '' },
    { field_id: 'acroform:attorney_name', kind: 'text', label: 'Attorney name', required: true, page: 1, rect: [320, 600, 540, 620], mine: false, value: 'Dana Reyes' },
    { field_id: 'acroform:client_signature', kind: 'signature', label: 'Client signature', required: true, page: 2, rect: [72, 120, 300, 150], mine: true },
    { field_id: 'acroform:client_initials', kind: 'initials', label: 'Client initials', required: true, page: 2, rect: [400, 120, 460, 150], mine: true },
    { field_id: 'field-0-0', kind: 'date', page: 2, rect: [320, 120, 400, 150], mine: true },
  ],
})
const today = () => new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })

beforeEach(() => {
  vi.resetAllMocks()
  api.get.mockResolvedValue({ data: new Blob(['%PDF-source']) })
  getClientPortalSignatureFields.mockResolvedValue(manifest())
  useTemplatePdfDocument.mockImplementation((source) => (source ? { document: {}, pages, error: '' } : { document: null, pages: [], error: '' }))
})
afterEach(cleanup)

describe('ClientSignatureDocument', () => {
  it('derives initials from the typed legal name', () => {
    expect(initialsFor('  jane  quinn smith ')).toBe('JQS')
    expect(initialsFor('')).toBe('')
  })

  it('lays the fields over their rects, gates signing on the required ones, and sends the values', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    signClientPortalSignature.mockResolvedValue({ id: 'req-1', status: 'completed', completion_pending: false })
    render(<ClientSignatureDocument request={request()} onChanged={onChanged} />)

    const name = await screen.findByLabelText('Client name')
    expect(api.get).toHaveBeenCalledWith('/portal/client/documents/fee/download', { responseType: 'blob' })
    expect(getClientPortalSignatureFields).toHaveBeenCalledWith('req-1')
    expect(screen.getAllByLabelText(/Rendered page/)).toHaveLength(2)

    // At 100% a PDF rect [x0, y0, x1, y1] lands at left x0 and top (792 - y1).
    fireEvent.change(screen.getByLabelText('Document zoom'), { target: { value: '1' } })
    expect(screen.getByLabelText('Rendered page 1')).toHaveTextContent('1 at 1')
    expect(name).toHaveStyle({ left: '72px', top: '172px', width: '228px', height: '20px' })
    const page2 = screen.getByTestId('signing-page-2')
    const signature = within(page2).getByRole('button', { name: 'Client signature' })
    expect(signature).toHaveStyle({ left: '72px', top: '642px', width: '228px', height: '30px' })
    expect(signature).toHaveTextContent('Click to sign')
    expect(within(page2).getByRole('button', { name: 'Client initials' })).toHaveTextContent('Click to initial')
    expect(screen.getByLabelText('Notes').tagName).toBe('TEXTAREA')
    expect(screen.getByLabelText('Date')).toHaveValue(today())
    expect(screen.getByLabelText('Date')).toHaveAttribute('readonly')
    const other = screen.getByLabelText('Attorney name (completed by another signer)')
    expect(other).toHaveValue('Dana Reyes')
    expect(other).toHaveAttribute('readonly')

    const sign = screen.getByRole('button', { name: 'Sign document' })
    expect(sign).toBeDisabled()
    expect(screen.getByText('0 of 5 required fields complete')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Type your full legal name — this becomes your signature'), 'Jane Quinn Smith')
    await user.click(screen.getByRole('checkbox', { name: /I consent to use an electronic signature/ }))
    expect(sign).toBeDisabled()

    await user.type(name, 'Jane Quinn Smith')
    await user.click(screen.getByLabelText('I agree'))
    await user.selectOptions(screen.getByLabelText('State'), 'IL')
    expect(screen.getByText('3 of 5 required fields complete')).toBeInTheDocument()
    expect(sign).toBeDisabled()

    await user.click(signature)
    expect(signature).toHaveAttribute('aria-pressed', 'true')
    const adopted = within(signature).getByText('Jane Quinn Smith')
    expect(adopted).toHaveClass('font-serif', 'italic')
    await user.click(within(page2).getByRole('button', { name: 'Client initials' }))
    expect(within(page2).getByRole('button', { name: 'Client initials' })).toHaveTextContent('JQS')
    expect(screen.getByText('5 of 5 required fields complete')).toBeInTheDocument()
    expect(sign).toBeEnabled()

    await user.click(sign)
    await waitFor(() => expect(signClientPortalSignature).toHaveBeenCalledOnce())
    expect(signClientPortalSignature).toHaveBeenCalledWith('req-1', {
      typed_signature: 'Jane Quinn Smith',
      consent_to_electronic_signature: true,
      consent_text_version: 'clarity-esign-consent-v1',
      // Only the acting signer's fillable fields travel; signature, initials
      // and date are stamped by the server and other signers' fields stay theirs.
      field_values: { 'acroform:client_name': 'Jane Quinn Smith', 'acroform:notes': '', 'acroform:agree': 'true', 'acroform:state': 'IL' },
    })
    expect(await screen.findByRole('status')).toHaveTextContent(SIGNED_MESSAGE)
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ status: 'completed' }))
  })

  it('asks for the legal name before a signature field can adopt it', async () => {
    const user = userEvent.setup()
    render(<ClientSignatureDocument request={request()} />)
    const signature = await screen.findByRole('button', { name: 'Client signature' })
    await user.click(signature)
    expect(signature).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('status')).toHaveTextContent('Type your full legal name in the bar below first')
    expect(screen.getByLabelText('Type your full legal name — this becomes your signature')).toHaveFocus()
  })

  it('tells the client the signature is recorded when filing is still pending, and surfaces server rejections', async () => {
    const user = userEvent.setup()
    getClientPortalSignatureFields.mockResolvedValue({ ...manifest(), fields: [manifest().fields[5]] })
    signClientPortalSignature
      .mockRejectedValueOnce({ response: { status: 422, data: { detail: 'Required fields are missing: State' } } })
      .mockResolvedValueOnce({ id: 'req-1', status: 'partially_signed', completion_pending: true })
    render(<ClientSignatureDocument request={request()} />)
    await user.type(await screen.findByLabelText('Type your full legal name — this becomes your signature'), 'Jane Smith')
    await user.click(screen.getByRole('checkbox', { name: /I consent/ }))
    await user.click(screen.getByRole('button', { name: 'Client signature' }))
    await user.click(screen.getByRole('button', { name: 'Sign document' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Required fields are missing: State')
    await user.click(screen.getByRole('button', { name: 'Sign document' }))
    expect(await screen.findByRole('status')).toHaveTextContent(FILING_PENDING_MESSAGE)
  })

  it('accepts a signed paper copy and waits for the firm to review it', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    uploadClientPortalSignedCopy.mockResolvedValue({ id: 'req-1', status: 'partially_signed', submitted_document_id: 'doc-9' })
    render(<ClientSignatureDocument request={request()} onChanged={onChanged} />)
    await screen.findByLabelText('Client name')
    expect(screen.getByRole('link', { name: 'Download document' })).toHaveAttribute('href', '/download/fee')
    const input = screen.getByLabelText(/Upload the signed copy \(PDF\)/)
    expect(input).toHaveAttribute('accept', 'application/pdf')
    const file = new File(['%PDF-1.7 signed'], 'signed.pdf', { type: 'application/pdf' })
    await user.upload(input, file)
    await waitFor(() => expect(uploadClientPortalSignedCopy).toHaveBeenCalledWith('req-1', file))
    expect(await screen.findByRole('status')).toHaveTextContent(SIGNED_COPY_RECEIVED_MESSAGE)
    expect(screen.queryByRole('button', { name: 'Sign document' })).not.toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ submitted_document_id: 'doc-9' }))
  })

  it('shows the awaiting-review state instead of the form once a copy was submitted', async () => {
    render(<ClientSignatureDocument request={{ ...request(), submitted_document_id: 'doc-9' }} />)
    expect(screen.getByRole('status')).toHaveTextContent(SIGNED_COPY_RECEIVED_MESSAGE)
    expect(screen.queryByLabelText(/Type your full legal name/)).not.toBeInTheDocument()
    expect(getClientPortalSignatureFields).toHaveBeenCalledWith('req-1')
  })

  it('falls back to paper plus a typed signature when the form cannot be filled in the browser', async () => {
    const user = userEvent.setup()
    getClientPortalSignatureFields.mockResolvedValue({ ...manifest(), fill_supported: false })
    signClientPortalSignature.mockResolvedValue({ id: 'req-1', status: 'completed' })
    render(<ClientSignatureDocument request={request()} />)
    expect(await screen.findByText(/This form cannot be filled in the browser/)).toBeInTheDocument()
    expect(screen.getAllByLabelText(/Rendered page/)).toHaveLength(2)
    expect(screen.queryByLabelText('Client name')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Upload the signed copy \(PDF\)/)).toBeInTheDocument()
    expect(screen.queryByText(/Prefer paper\?/)).not.toBeInTheDocument()
    const sign = screen.getByRole('button', { name: 'Sign document' })
    expect(sign).toBeDisabled()
    await user.type(screen.getByLabelText(/Type your full legal name/), 'Jane Smith')
    await user.click(screen.getByRole('checkbox', { name: /I consent/ }))
    expect(sign).toBeEnabled()
    await user.click(sign)
    await waitFor(() => expect(signClientPortalSignature).toHaveBeenCalledWith('req-1', expect.objectContaining({ typed_signature: 'Jane Smith', field_values: {} })))
  })

  it('stamps a drawn signature at the signature fields and sends it with the typed name', async () => {
    const user = userEvent.setup()
    const drawn = 'data:image/png;base64,iVBORw0KGgo='
    const context = { scale: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), clearRect: vi.fn() }
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context)
    const toDataURL = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue(drawn)
    getClientPortalSignatureFields.mockResolvedValue({
      ...manifest(),
      fields: manifest().fields.filter((field) => ['acroform:client_signature', 'acroform:client_initials', 'field-0-0'].includes(field.field_id)),
    })
    signClientPortalSignature.mockResolvedValue({ id: 'req-1', status: 'completed', completion_pending: false })
    try {
      render(<ClientSignatureDocument request={request()} onChanged={vi.fn()} />)
      const signatureField = await screen.findByRole('button', { name: 'Client signature' })

      await user.click(screen.getByRole('radio', { name: 'Drawing my signature' }))
      const pad = screen.getByRole('img', { name: 'Draw your signature' })
      // The signer cannot sign with an empty pad even once the name is typed.
      await user.type(screen.getByLabelText(/Type your full legal name/), 'Jane Quinn Smith')
      await user.click(screen.getByRole('checkbox', { name: /I consent/ }))
      expect(screen.getByRole('button', { name: 'Sign document' })).toBeDisabled()

      fireEvent.pointerDown(pad, { clientX: 20, clientY: 80, pointerId: 1 })
      fireEvent.pointerMove(pad, { clientX: 120, clientY: 40, pointerId: 1 })
      fireEvent.pointerUp(pad, { clientX: 120, clientY: 40, pointerId: 1 })
      expect(context.lineTo).toHaveBeenCalled()
      expect(toDataURL).toHaveBeenCalledWith('image/png')

      await user.click(signatureField)
      await user.click(screen.getByRole('button', { name: 'Client initials' }))
      // The drawing appears at the signature line; initials stay typed.
      expect(within(signatureField).getByRole('img', { name: /Your drawn signature/ })).toHaveAttribute('src', drawn)
      expect(screen.getByRole('button', { name: 'Client initials' })).toHaveTextContent('JQS')

      const sign = screen.getByRole('button', { name: 'Sign document' })
      expect(sign).toBeEnabled()
      await user.click(sign)
      await waitFor(() => expect(signClientPortalSignature).toHaveBeenCalledWith('req-1', expect.objectContaining({
        typed_signature: 'Jane Quinn Smith',
        drawn_signature_png: drawn,
      })))

      // Clearing the pad drops the drawing and the typed name takes over again.
    } finally {
      getContext.mockRestore()
      toDataURL.mockRestore()
    }
  })

  it('clearing the pad falls back to the typed name and sends no drawing', async () => {
    const user = userEvent.setup()
    const context = { scale: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), clearRect: vi.fn() }
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context)
    const toDataURL = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,iVBORw0KGgo=')
    getClientPortalSignatureFields.mockResolvedValue({ ...manifest(), fill_supported: false })
    signClientPortalSignature.mockResolvedValue({ id: 'req-1', status: 'completed' })
    try {
      render(<ClientSignatureDocument request={request()} />)
      await screen.findByText(/This form cannot be filled in the browser/)
      await user.click(screen.getByRole('radio', { name: 'Drawing my signature' }))
      const pad = screen.getByRole('img', { name: 'Draw your signature' })
      fireEvent.pointerDown(pad, { clientX: 20, clientY: 80, pointerId: 1 })
      fireEvent.pointerUp(pad, { clientX: 60, clientY: 40, pointerId: 1 })
      await user.click(screen.getByRole('button', { name: 'Clear' }))
      expect(context.clearRect).toHaveBeenCalled()
      await user.type(screen.getByLabelText(/Type your full legal name/), 'Jane Smith')
      await user.click(screen.getByRole('checkbox', { name: /I consent/ }))
      // An empty pad in draw mode blocks signing; switching back to typing does not.
      expect(screen.getByRole('button', { name: 'Sign document' })).toBeDisabled()
      await user.click(screen.getByRole('radio', { name: 'Typing my name' }))
      await user.click(screen.getByRole('button', { name: 'Sign document' }))
      await waitFor(() => expect(signClientPortalSignature).toHaveBeenCalledWith('req-1', expect.objectContaining({ typed_signature: 'Jane Smith' })))
      expect(signClientPortalSignature.mock.calls[0][1]).not.toHaveProperty('drawn_signature_png')
    } finally {
      getContext.mockRestore()
      toDataURL.mockRestore()
    }
  })

  it('says so when the browser cannot draw and keeps the typed path', async () => {
    const user = userEvent.setup()
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    try {
      render(<ClientSignatureDocument request={request()} />)
      await screen.findByLabelText('Client name')
      await user.click(screen.getByRole('radio', { name: 'Drawing my signature' }))
      expect(screen.getByRole('status')).toHaveTextContent('Drawing is not available in this browser')
      expect(screen.queryByRole('img', { name: 'Draw your signature' })).not.toBeInTheDocument()
    } finally {
      getContext.mockRestore()
    }
  })

  it('never dead-ends when the field manifest cannot be loaded', async () => {
    getClientPortalSignatureFields.mockRejectedValue(new Error('offline'))
    render(<ClientSignatureDocument request={request()} />)
    expect(await screen.findByText(/This form cannot be filled in the browser/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Upload the signed copy \(PDF\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign document' })).toBeInTheDocument()
  })

  it('offers a retry and the paper path when the document itself fails to load', async () => {
    const user = userEvent.setup()
    api.get.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: new Blob(['%PDF-source']) })
    render(<ClientSignatureDocument request={request()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The document could not be loaded')
    expect(screen.getByText(/could not be displayed in the browser/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Upload the signed copy \(PDF\)/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry document' }))
    expect(await screen.findByLabelText('Client name')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports a session problem to the host instead of falling back', async () => {
    const onSessionError = vi.fn(() => true)
    getClientPortalSignatureFields.mockRejectedValue({ response: { status: 401 } })
    render(<ClientSignatureDocument request={request()} onSessionError={onSessionError} />)
    await waitFor(() => expect(onSessionError).toHaveBeenCalled())
    expect(screen.queryByText(/This form cannot be filled in the browser/)).not.toBeInTheDocument()
  })
})
