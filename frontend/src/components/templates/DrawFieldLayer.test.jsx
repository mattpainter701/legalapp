import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DrawFieldLayer, { drawnRect } from './DrawFieldLayer'
afterEach(cleanup)
describe('draw template boxes', () => {
  it('normalizes reverse drags and clamps geometry to the page', () => { expect(drawnRect({ x: 90, y: 70 }, { x: -5, y: 200 }, 100, 100)).toEqual({ x: 0, y: 70, width: 90, height: 30 }) })
  it('offers keyboard creation, names the variable, and rejects duplicate names', () => {
    const create = vi.fn()
    render(<DrawFieldLayer mode="field" width={600} height={800} names={['client_name']} onCreate={create} onCancel={vi.fn()} />)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Draw field rectangle' }), { key: 'Enter' })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'client_name' } }); fireEvent.click(screen.getByText('Create field'))
    expect(screen.getByRole('alert')).toHaveTextContent('already exists'); expect(create).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'matter_name' } }); fireEvent.click(screen.getByText('Create field'))
    expect(create).toHaveBeenCalledWith({ x: 150, y: 800 / 3, width: 200, height: 24 }, 'matter_name')
  })
  it('cancels without creating an orphan field', () => { const create = vi.fn(), cancel = vi.fn(); render(<DrawFieldLayer mode="field" width={600} height={800} names={[]} onCreate={create} onCancel={cancel} />); fireEvent.keyDown(screen.getByRole('button', { name: 'Draw field rectangle' }), { key: 'Escape' }); expect(cancel).toHaveBeenCalled(); expect(create).not.toHaveBeenCalled() })
})

it('draws a whiteout with real pointer coordinates and ignores tiny gestures', () => {
  const create = vi.fn()
  render(<DrawFieldLayer mode="whiteout" width={600} height={800} names={[]} onCreate={create} onCancel={vi.fn()} />)
  const layer = screen.getByRole('button', { name: 'Draw whiteout rectangle' })
  layer.getBoundingClientRect = () => ({ left: 10, top: 20 })
  fireEvent.pointerDown(layer, { button: 0, pointerId: 1, clientX: 30, clientY: 50 })
  fireEvent.pointerUp(layer, { pointerId: 1, clientX: 32, clientY: 51 })
  expect(create).not.toHaveBeenCalled()
  fireEvent.pointerDown(layer, { button: 0, pointerId: 2, clientX: 210, clientY: 120 })
  fireEvent.pointerMove(layer, { pointerId: 2, clientX: 30, clientY: 50 })
  fireEvent.pointerUp(layer, { pointerId: 2, clientX: 30, clientY: 50 })
  expect(create).toHaveBeenCalledWith({ x: 20, y: 30, width: 180, height: 70 })
})
