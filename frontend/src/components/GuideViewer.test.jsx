import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import GuideViewer from './GuideViewer'
import { ADMINISTRATIVE_GUIDE, USER_GUIDE } from '../platformDocs'

describe('GuideViewer', () => {
  it('searches across chapter content and opens the result', () => {
    render(
      <MemoryRouter>
        <GuideViewer documents={USER_GUIDE} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Start here' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search this guide' }), {
      target: { value: 'specialized practice workflows' },
    })
    const chapterNavigation = within(screen.getByLabelText('Guide chapters'))
    expect(chapterNavigation.getByRole('button', { name: /Assistant & add-ons/ })).toBeInTheDocument()
    expect(chapterNavigation.queryByRole('button', { name: /Matters & documents/ })).not.toBeInTheDocument()
    fireEvent.click(chapterNavigation.getByRole('button', { name: /Assistant & add-ons/ }))
    expect(screen.getByRole('heading', { name: 'Assistant & add-ons' })).toBeInTheDocument()
  })

  it('renders admin deep links as application links', () => {
    render(
      <MemoryRouter>
        <GuideViewer documents={ADMINISTRATIVE_GUIDE} audience="admin" />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Users' })).toHaveAttribute('href', '/admin?tab=users')
    expect(screen.getByLabelText('Administrative guide')).toBeInTheDocument()
  })
})
