import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AppErrorBoundary from './AppErrorBoundary'

function BrokenView() {
  throw new Error('render exploded')
}

describe('AppErrorBoundary', () => {
  it('keeps a render failure from blanking the workspace', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<AppErrorBoundary><BrokenView /></AppErrorBoundary>)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /could not finish rendering/i })).toBeInTheDocument()
    expect(screen.getByText(/stopped before it could finish rendering/i)).toBeInTheDocument()
    expect(screen.queryByText(/saved records were not changed/i)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Return to sign in' })).toHaveAttribute('href', '/login')
    consoleSpy.mockRestore()
  })
})
