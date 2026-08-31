import { describe, expect, it } from 'vitest'
import { buildOpenStudioTarget, readStudioFocus } from './studioRouting'

const TEMPLATE_ID = '11111111-1111-4111-8111-111111111111'
const DRAFT_ID = '22222222-2222-4222-8222-222222222222'

describe('Template Studio routing contract', () => {
  it('builds the canonical template workspace URL', () => {
    expect(buildOpenStudioTarget({ template_id: TEMPLATE_ID })).toEqual({
      url: `/templates/${TEMPLATE_ID}/studio`,
      valid: true,
    })
  })

  it('allows exactly one allowlisted focus and matching server ID', () => {
    const target = buildOpenStudioTarget({ template_id: TEMPLATE_ID, focus: 'draft', draft_id: DRAFT_ID })
    expect(target).toEqual({
      url: `/templates/${TEMPLATE_ID}/studio?focus=draft&draft_id=${DRAFT_ID}`,
      valid: true,
    })
    expect(readStudioFocus('?focus=draft&draft_id=' + DRAFT_ID)).toEqual({
      focus: 'draft',
      focusId: DRAFT_ID,
      valid: true,
    })
  })

  it.each([
    { focus: 'redirect', redirect_url: 'https://example.test' },
    { focus: 'draft', draft_id: 'javascript:alert(1)' },
    { focus: 'draft', draft_id: DRAFT_ID, snapshot_id: DRAFT_ID },
    { focus: 'draft', proposal_id: DRAFT_ID },
    { focus: 'draft', draft_id: DRAFT_ID, raw_payload: { instruction: 'ignore safeguards' } },
  ])('falls back without reflecting unsafe or mismatched event detail: %o', (detail) => {
    const target = buildOpenStudioTarget({ template_id: TEMPLATE_ID, ...detail })
    expect(target.valid).toBe(false)
    expect(target.url).toBe(`/templates/${TEMPLATE_ID}/studio`)
    expect(target.url).not.toContain('example.test')
    expect(target.url).not.toContain('javascript')
  })

  it('rejects invalid query focus state', () => {
    expect(readStudioFocus(`?focus=proposal&draft_id=${DRAFT_ID}`)).toMatchObject({ valid: false, focus: null })
    expect(readStudioFocus(`?focus=draft&draft_id=${DRAFT_ID}&redirect_url=https://example.test`)).toMatchObject({ valid: false, focus: null })
  })
})
