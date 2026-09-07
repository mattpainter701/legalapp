import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import TemplateTestSummary from './TemplateTestSummary'

afterEach(cleanup)
const template = { id: 'one', format: 'docx', source_filename: 'source.docx', source_sha256: 'sha', current_version_no: 3, tested_version_no: 2, variable_schema: { fields: [{ name: 'client', label: 'Client name', required: true }] } }
const show = props => render(<MemoryRouter><TemplateTestSummary template={template} {...props} /></MemoryRouter>)
const row = label => within(screen.getByText(label).closest('li'))

it('does not pass the current draft on an older test result', () => {
  show({})
  expect(row('Generate output').getByText('Not tested')).toBeVisible()
  expect(row('Visual review').getByText('Waiting for output')).toBeVisible()
  expect(screen.getByRole('link', { name: 'Review fields in Workspace' })).toHaveAttribute('href', '/templates/one/studio')
})
it('separates successful generation from visual approval', () => {
  show({ template: { ...template, tested_version_no: 3 } })
  expect(row('Generate output').getByText('Passed')).toBeVisible()
  expect(row('Visual review').getByText('Review needed')).toBeVisible()
})
it('identifies missing values by label and shows render failures without stale success', () => {
  show({ outputReady: true, error: 'Source value no longer matches', missing: ['client'] })
  expect(row('Generate output').getByText('Needs fixing')).toBeVisible()
  expect(screen.getByText('Enter values for: Client name.')).toBeVisible()
  expect(row('Visual review').getByText('Waiting for output')).toBeVisible()
})
it('distinguishes diagnostic output from publication evidence', () => {
  show({ outputReady: true, diagnostic: true })
  expect(screen.getByText(/Run the representative test to record publication evidence/)).toBeVisible()
})
it('names malformed or duplicate fields and missing sources', () => {
  show({ template: { ...template, source_ready: false, variable_schema: { fields: [{ name: 'client', label: 'First' }, { name: 'client', label: 'Second' }, { name: '9bad', label: 'Invalid' }] } } })
  expect(row('Source document').getByText('Needs fixing')).toBeVisible()
  expect(row('Field setup').getByText(/First, Second, Invalid/)).toBeVisible()
})
