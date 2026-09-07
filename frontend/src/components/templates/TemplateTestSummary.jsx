import { Link } from 'react-router-dom'

/** Report evidence we actually have; rendering success is not visual approval. */
export default function TemplateTestSummary({ template, error, rendering = false, outputReady, missing = [], diagnostic = false, onFixFields }) {
  const fields = (template.variable_schema?.fields || []).filter(field => field?.included !== false)
  const names = fields.map(field => field.name)
  const invalid = fields.filter(field => !/^[A-Za-z][A-Za-z0-9_.-]*$/.test(field.name || '') || names.filter(name => name === field.name).length > 1)
  const currentPassed = template.current_version_no > 0 && template.tested_version_no === template.current_version_no
  const sourceReady = template.source_ready !== false && (!['docx', 'pdf'].includes(template.format) || Boolean(template.source_filename && template.source_sha256))
  const passed = !error && !missing.length && (outputReady === undefined ? currentPassed : outputReady)
  const failed = Boolean(error) || (outputReady === undefined && template.status === 'test_failed' && !currentPassed)
  const fieldLabel = name => fields.find(field => field.name === name)?.label || name
  const rows = [
    { label: 'Source document', status: sourceReady ? 'Passed' : 'Needs fixing', detail: sourceReady ? 'Source is available for generation.' : 'Restore or re-import the original document.' },
    { label: 'Field setup', status: invalid.length ? 'Needs fixing' : 'Passed', detail: invalid.length ? `Give these fields unique, valid names: ${invalid.map(field => field.label || field.name || 'Unnamed field').join(', ')}.` : `${fields.length} included field${fields.length === 1 ? '' : 's'}. Check the highlighted locations in Workspace.` },
    ...(missing.length ? [{ label: 'Test values', status: 'Needs fixing', detail: `Enter values for: ${missing.map(fieldLabel).join(', ')}.` }] : []),
    { label: 'Generate output', status: rendering ? 'Running' : failed ? 'Needs fixing' : passed ? 'Passed' : 'Not tested', detail: error || (failed ? 'The latest test failed. Run it again to see the current error.' : passed ? diagnostic ? 'Draft output generated. Run the representative test to record publication evidence.' : `Output generated for version ${template.current_version_no || 'the current draft'}.` : 'Run a test with sample values. An earlier version’s result does not test this draft.') },
    { label: 'Visual review', status: passed ? 'Review needed' : 'Waiting for output', detail: 'Inspect every generated page for correct values, clipping, page breaks and signing locations. This check is yours to complete before publishing.' },
  ]
  return <section aria-label="Test results" className="mt-4 rounded-xl border border-brand-line bg-brand-bg p-4">
    <h3 className="font-semibold text-brand-ink">What passed and what needs attention</h3>
    <ul className="mt-3 space-y-3" aria-live="polite">
      {rows.map(row => <li key={row.label} className="text-sm">
        <div className="flex flex-wrap justify-between gap-2"><strong>{row.label}</strong><span className={row.status === 'Passed' ? 'font-semibold text-brand-green' : 'font-semibold text-brand-ink'}>{row.status}</span></div>
        <p className="mt-1 text-xs leading-5 text-brand-muted">{row.detail}</p>
      </li>)}
    </ul>
    {onFixFields && <button type="button" onClick={onFixFields} className="mt-3 rounded border border-brand-line px-3 py-2 text-sm font-semibold">Review fields</button>}
    {!onFixFields && outputReady === undefined && <Link to={`/templates/${template.id}/studio`} className="mt-3 inline-block text-sm font-semibold underline">Review fields in Workspace</Link>}
  </section>
}
