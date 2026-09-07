import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DocxDocumentView, {
  anchorsByParagraph,
  regionsFromMarkers,
  regionsFromStore,
  segmentsFor,
} from './DocxDocumentView'
import { getTemplateOutline } from '../../api'

vi.mock('../../api', () => ({ getTemplateOutline: vi.fn() }))

const outline = (paragraphs) => ({
  template_id: 't1',
  paragraphs: paragraphs.map((p, index) => ({
    ordinal: index,
    text: p.text ?? '',
    style: p.style || 'Normal',
    container: p.container || 'body',
    runs: [],
    marker: p.marker || null,
  })),
  paragraph_count: paragraphs.length,
  truncated: false,
})

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('anchorsByParagraph', () => {
  it('groups anchored fields by paragraph, in span order', () => {
    const grouped = anchorsByParagraph([
      { name: 'b', docx_anchor: { paragraph_ordinal: 1, start: 10, end: 14 } },
      { name: 'a', docx_anchor: { paragraph_ordinal: 1, start: 0, end: 4 } },
      { name: 'c', docx_anchor: { paragraph_ordinal: 3, start: 2, end: 5 } },
    ])
    expect(grouped.get(1).map((s) => s.field.name)).toEqual(['a', 'b'])
    expect(grouped.get(3)).toHaveLength(1)
  })

  it('ignores fields with no anchor, excluded fields, and malformed spans', () => {
    const grouped = anchorsByParagraph([
      { name: 'placeholder' },
      { name: 'excluded', included: false, docx_anchor: { paragraph_ordinal: 0, start: 0, end: 4 } },
      { name: 'inverted', docx_anchor: { paragraph_ordinal: 0, start: 9, end: 2 } },
      { name: 'unparseable', docx_anchor: { paragraph_ordinal: 'x', start: 0, end: 4 } },
    ])
    expect(grouped.size).toBe(0)
  })

  it('tolerates a missing field list', () => {
    expect(anchorsByParagraph(undefined).size).toBe(0)
  })
})

describe('regionsFromMarkers', () => {
  it('pairs markers and records nesting depth', () => {
    const regions = regionsFromMarkers(outline([
      { marker: { kind: 'open', keyword: 'if', name: 'a' } },
      { marker: { kind: 'open', keyword: 'each', name: 'parties' } },
      { text: 'inner' },
      { marker: { kind: 'close', keyword: 'each' } },
      { marker: { kind: 'close', keyword: 'if' } },
    ]).paragraphs)
    expect(regions).toEqual([
      { kind: 'open', keyword: 'each', name: 'parties', from: 1, to: 3, depth: 1 },
      { kind: 'open', keyword: 'if', name: 'a', from: 0, to: 4, depth: 0 },
    ])
  })

  it('leaves unbalanced markers unpaired rather than guessing', () => {
    // The renderer rejects these too; drawing a region would hide the problem.
    expect(regionsFromMarkers(outline([
      { marker: { kind: 'open', keyword: 'if', name: 'a' } },
      { text: 'never closed' },
    ]).paragraphs)).toEqual([])
    expect(regionsFromMarkers(outline([
      { marker: { kind: 'close', keyword: 'if' } },
    ]).paragraphs)).toEqual([])
  })

  it('does not pair mismatched keywords', () => {
    expect(regionsFromMarkers(outline([
      { marker: { kind: 'open', keyword: 'if', name: 'a' } },
      { marker: { kind: 'close', keyword: 'each' } },
    ]).paragraphs)).toEqual([])
  })
})

describe('segmentsFor', () => {
  it('splits a paragraph into plain and field pieces', () => {
    const segments = segmentsFor('Dear Ada Lovelace, welcome', [
      { start: 5, end: 17, field: { name: 'client' } },
    ])
    expect(segments.map((s) => s.text)).toEqual(['Dear ', 'Ada Lovelace', ', welcome'])
    expect(segments[1].field.name).toBe('client')
  })

  it('clamps a span that runs past the paragraph', () => {
    // A stale anchor must not throw or produce a negative slice.
    const segments = segmentsFor('short', [{ start: 2, end: 99, field: { name: 'x' } }])
    expect(segments.map((s) => s.text).join('')).toBe('short')
  })

  it('returns the whole paragraph when nothing is anchored', () => {
    expect(segmentsFor('plain text', [])).toEqual([{ text: 'plain text', start: 0 }])
  })
})

describe('DocxDocumentView', () => {
  it.each(['table', 'footer'])('selects Unicode source offsets in a %s without including UI labels', async (container) => {
    getTemplateOutline.mockResolvedValue(outline([{ text: '😀 Fee: ___', container }]))
    const onCreateField = vi.fn()
    render(<DocxDocumentView templateId="t1" fields={[]} onCreateField={onCreateField} />)
    await screen.findByText('😀 Fee: ___')
    const source = document.querySelector('[data-source-text]')
    expect(source.textContent).toBe('😀 Fee: ___')
    const node = document.createTreeWalker(source, NodeFilter.SHOW_TEXT).nextNode()
    const range = document.createRange()
    range.setStart(node, 8)
    range.setEnd(node, 11)
    globalThis.getSelection().removeAllRanges()
    globalThis.getSelection().addRange(range)
    fireEvent.mouseUp(source)
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }))
    expect(onCreateField).toHaveBeenCalledWith({ ordinal: 0, start: 7, end: 10, text: '___' })
  })

  it('renders source-order tables, styles, numbering and global token highlights', async () => {
    const data = outline([{ text: 'First' }, { text: 'Last' }, { text: '[AMOUNT]', container: 'table' }])
    data.paragraphs[0].numbering = '1.'
    data.paragraphs[0].runs = [{ start: 0, end: 5, bold: true, italic: true, underline: true }]
    data.paragraphs[1].dynamic_field = true
    data.blocks = [{ kind: 'paragraph', ordinal: 0 }, { kind: 'table', rows: [{ cells: [{ colspan: 2, rowspan: 2, blocks: [{ kind: 'paragraph', ordinal: 2 }] }] }] }, { kind: 'paragraph', ordinal: 1 }]
    getTemplateOutline.mockResolvedValue(data)
    render(<DocxDocumentView templateId="t1" fields={[{ name: 'amount', source_text: '[AMOUNT]' }]} />)
    const token = await screen.findByRole('button', { name: '[AMOUNT]' })
    expect(token.closest('td')).toHaveAttribute('colspan', '2')
    expect([...document.querySelectorAll('[data-ordinal]')].map(node => node.dataset.ordinal)).toEqual(['0', '2', '1'])
    expect(screen.getByText('First')).toHaveStyle({ fontWeight: 'bold', fontStyle: 'italic', textDecoration: 'underline' })
    expect(screen.getByText('1.')).toBeInTheDocument()
    expect(screen.getByText(/Page fields update/)).toBeInTheDocument()
  })

  it('records a source disposition and creates a field from its verified span', async () => {
    const candidate = { id: 'source-id', kind: 'blank', source_text: '___', context: 'Signature: ___', docx_anchor: { paragraph_ordinal: 0, start: 11, end: 14 } }
    getTemplateOutline.mockResolvedValue({ ...outline([{ text: candidate.context }]), review_candidates: [candidate] })
    const onReviewChange = vi.fn()
    const onCreateField = vi.fn()
    const { rerender } = render(<DocxDocumentView templateId="t1" fields={[]} onReviewChange={onReviewChange} onCreateField={onCreateField} />)
    expect(await screen.findByText('Source review: 1 details to review')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: /Review blank/ }), { target: { value: 'signature' } })
    expect(onReviewChange).toHaveBeenCalledWith({ 'source-id': 'signature' })
    fireEvent.click(screen.getByRole('button', { name: 'Make field' }))
    expect(onCreateField).toHaveBeenCalledWith({ ordinal: 0, start: 11, end: 14, text: '___' })
    rerender(<DocxDocumentView templateId="t1" fields={[]} sourceReview={{ 'source-id': 'signature' }} onReviewChange={onReviewChange} />)
    expect(screen.getByText('Source review: 0 details to review')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Show reviewed details'))
    fireEvent.change(screen.getByRole('combobox', { name: /Review blank/ }), { target: { value: '' } })
    expect(onReviewChange).toHaveBeenLastCalledWith({})
  })

  it('highlights global tokens using Python character offsets and excludes overlapping maps', () => {
    const paragraphs = [{ ordinal: 0, text: '😀[DATE] [DATE]' }]
    const fields = [{ name: 'first', docx_anchor: { paragraph_ordinal: 0, start: 1, end: 7 } }, { name: 'all', source_text: '[DATE]' }, { name: 'excluded', included: false, source_text: '[DATE]' }]
    const spans = anchorsByParagraph(fields, paragraphs).get(0)
    expect(spans.map(span => [span.field.name, span.start, span.end])).toEqual([['first', 1, 7], ['all', 8, 14]])
    expect(segmentsFor(paragraphs[0].text, spans).map(segment => segment.text).join('')).toBe(paragraphs[0].text)
  })
  it('renders the document with mapped fields highlighted', async () => {
    getTemplateOutline.mockResolvedValue(outline([
      { text: 'ENGAGEMENT LETTER', style: 'Heading 1' },
      { text: 'Client: Ada Lovelace' },
    ]))
    render(
      <DocxDocumentView
        templateId="t1"
        fields={[{ name: 'client_name', docx_anchor: { paragraph_ordinal: 1, start: 8, end: 20 } }]}
      />,
    )
    expect(await screen.findByText('ENGAGEMENT LETTER')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ada Lovelace' })).toBeInTheDocument()
  })

  it('describes a logic marker instead of showing its syntax as prose', async () => {
    getTemplateOutline.mockResolvedValue(outline([
      { text: '{{#each parties}}', marker: { kind: 'open', keyword: 'each', name: 'parties' } },
      { text: '{{/each}}', marker: { kind: 'close', keyword: 'each' } },
    ]))
    render(<DocxDocumentView templateId="t1" fields={[]} />)

    expect(await screen.findByText(/Repeat for each parties/i)).toBeInTheDocument()
    expect(screen.getByText(/End each/i)).toBeInTheDocument()
  })

  it('labels paragraphs that live outside the body', async () => {
    getTemplateOutline.mockResolvedValue(outline([
      { text: 'Firm letterhead', container: 'footer' },
    ]))
    render(<DocxDocumentView templateId="t1" fields={[]} />)
    expect(await screen.findByText('Footer')).toBeInTheDocument()
  })

  it('selects the field a highlighted span belongs to', async () => {
    getTemplateOutline.mockResolvedValue(outline([{ text: 'Client: Ada' }]))
    const onSelectField = vi.fn()
    render(
      <DocxDocumentView
        templateId="t1"
        fields={[{ name: 'client_name', docx_anchor: { paragraph_ordinal: 0, start: 8, end: 11 } }]}
        onSelectField={onSelectField}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Ada' }))
    expect(onSelectField).toHaveBeenCalledWith(expect.objectContaining({ name: 'client_name' }))
  })

  it('surfaces a read failure with the server’s reason', async () => {
    getTemplateOutline.mockRejectedValue({
      response: { data: { detail: 'The original template file is unavailable' } },
    })
    render(<DocxDocumentView templateId="t1" fields={[]} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/unavailable/i)
  })

  it('shows a loading state while the document is read', () => {
    getTemplateOutline.mockReturnValue(new Promise(() => {}))
    render(<DocxDocumentView templateId="t1" fields={[]} />)
    expect(screen.getByRole('status', { name: 'Document loading status' })).toBeInTheDocument()
  })
})

describe('regionsFromStore', () => {
  it('normalises stored ranges into the marker region shape', () => {
    expect(regionsFromStore([
      { kind: 'each', name: 'parties', from_ordinal: 4, to_ordinal: 6 },
    ])).toEqual([
      { keyword: 'each', name: 'parties', from: 4, to: 6, stored: true },
    ])
  })

  it('drops ranges the renderer would refuse', () => {
    expect(regionsFromStore([
      null,
      { kind: 'switch', name: 'a', from_ordinal: 0, to_ordinal: 1 },
      { kind: 'if', name: 'a', from_ordinal: 4, to_ordinal: 2 },
      { kind: 'if', name: 'a', from_ordinal: '0', to_ordinal: 1 },
    ])).toEqual([])
  })

  it('tolerates a template with no regions', () => {
    expect(regionsFromStore(undefined)).toEqual([])
  })
})

describe('DocxDocumentView regions', () => {
  const paragraphs = outline([
    { text: 'Signed:' },
    { text: 'PARTY, as ROLE' },
    { text: 'Tail' },
  ])

  it('marks a paragraph range as repeating', async () => {
    getTemplateOutline.mockResolvedValue(paragraphs)
    const onCreateRegion = vi.fn()
    render(
      <DocxDocumentView
        templateId="t1"
        fields={[]}
        collections={[{ name: 'parties', label: 'All matter parties' }]}
        conditionFields={['is_entity']}
        onCreateRegion={onCreateRegion}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Select paragraph 2' }))
    expect(screen.getByText(/1 paragraph selected/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Repeat for each'), { target: { value: 'parties' } })
    expect(onCreateRegion).toHaveBeenCalledWith({
      kind: 'each', name: 'parties', from_ordinal: 1, to_ordinal: 1,
    })
  })

  it('extends the range with a shift-click and conditions it', async () => {
    getTemplateOutline.mockResolvedValue(paragraphs)
    const onCreateRegion = vi.fn()
    render(
      <DocxDocumentView
        templateId="t1"
        fields={[]}
        collections={[]}
        conditionFields={['is_entity']}
        onCreateRegion={onCreateRegion}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Select paragraph 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Select paragraph 3' }), { shiftKey: true })
    expect(screen.getByText(/3 paragraphs selected/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Only include when'), { target: { value: 'is_entity' } })
    expect(onCreateRegion).toHaveBeenCalledWith({
      kind: 'if', name: 'is_entity', from_ordinal: 0, to_ordinal: 2,
    })
  })

  it('shows a stored region as a band on the paragraph it opens', async () => {
    getTemplateOutline.mockResolvedValue(paragraphs)
    render(
      <DocxDocumentView
        templateId="t1"
        fields={[]}
        regions={[{ kind: 'each', name: 'parties', from_ordinal: 1, to_ordinal: 1 }]}
      />,
    )
    expect(await screen.findByText(/Repeat for each parties/i)).toBeInTheDocument()
  })

  it('removes a stored region', async () => {
    getTemplateOutline.mockResolvedValue(paragraphs)
    const onRemoveRegion = vi.fn()
    render(
      <DocxDocumentView
        templateId="t1"
        fields={[]}
        regions={[{ kind: 'if', name: 'is_entity', from_ordinal: 0, to_ordinal: 2 }]}
        onRemoveRegion={onRemoveRegion}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: /remove/i }))
    expect(onRemoveRegion).toHaveBeenCalledWith(
      expect.objectContaining({ keyword: 'if', name: 'is_entity', from: 0, to: 2 }),
    )
  })

  it('cancels a paragraph selection without creating anything', async () => {
    getTemplateOutline.mockResolvedValue(paragraphs)
    const onCreateRegion = vi.fn()
    render(<DocxDocumentView templateId="t1" fields={[]} onCreateRegion={onCreateRegion} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Select paragraph 1' }))
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.queryByText(/paragraph selected/)).not.toBeInTheDocument()
    expect(onCreateRegion).not.toHaveBeenCalled()
  })
})
