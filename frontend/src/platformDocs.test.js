import { describe, expect, it } from 'vitest'
import { ADMINISTRATIVE_GUIDE, USER_GUIDE, parseGuideChapter, slugifyHeading } from './platformDocs'

describe('platform guides', () => {
  it('loads and orders both guide audiences', () => {
    expect(USER_GUIDE).toHaveLength(16)
    expect(ADMINISTRATIVE_GUIDE).toHaveLength(16)
    expect(USER_GUIDE.map((chapter) => chapter.order)).toEqual(
      Array.from({ length: 16 }, (_, index) => (index + 1) * 10),
    )
    expect(ADMINISTRATIVE_GUIDE.map((chapter) => chapter.order)).toEqual(
      Array.from({ length: 16 }, (_, index) => (index + 1) * 10),
    )
    expect(ADMINISTRATIVE_GUIDE.every((chapter) => chapter.audience === 'admin')).toBe(true)
  })

  it('keeps chapter slugs unique within each audience', () => {
    expect(new Set(USER_GUIDE.map((chapter) => chapter.slug)).size).toBe(USER_GUIDE.length)
    expect(new Set(ADMINISTRATIVE_GUIDE.map((chapter) => chapter.slug)).size).toBe(ADMINISTRATIVE_GUIDE.length)
  })

  it('parses headings for the in-page navigation', () => {
    const chapter = parseGuideChapter(
      '---\nslug: sample\ntitle: Sample\ndescription: Example\norder: 1\nread_time: 1 min\nicon: compass\n---\n# Sample\n\n## First step',
      'sample.md',
      'user',
    )
    expect(chapter.headings).toEqual([{ title: 'First step', id: 'first-step' }])
    expect(slugifyHeading('AI, Search & MCP')).toBe('ai-search-mcp')
  })
})
