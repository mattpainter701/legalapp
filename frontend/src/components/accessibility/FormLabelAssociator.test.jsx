import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import FormLabelAssociator from './FormLabelAssociator'

describe('legacy form label association', () => {
  it('associates a nearby unbound label without overriding explicit labels', async () => {
    render(<><FormLabelAssociator /><div><label>Client name</label><input /></div><div><label htmlFor="explicit">Matter</label><input id="explicit" /></div></>)
    expect(await screen.findByLabelText('Client name')).toBeInTheDocument()
    expect(screen.getByLabelText('Matter')).toHaveAttribute('id', 'explicit')
  })
})
