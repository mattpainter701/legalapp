import { test, expect } from '@playwright/test'
import { dismissReleaseAnnouncement } from './release-announcement.js'
const id = '00000000-0000-4000-8000-000000000001'
const docId = '00000000-0000-4000-8000-000000000002'
const folderId = '00000000-0000-4000-8000-000000000003'
const json = (route, data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) })
test('Jane Doe workspace keeps fields optional and documents reachable', async ({ page }) => {
  const copies = []
  const pdf = Buffer.from('JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgaHR0cDovL3d3dy5yZXBvcnRsYWIuY29tCjEgMCBvYmoKPDwKL0YxIDIgMCBSCj4+CmVuZG9iagoyIDAgb2JqCjw8Ci9CYXNlRm9udCAvSGVsdmV0aWNhIC9FbmNvZGluZyAvV2luQW5zaUVuY29kaW5nIC9OYW1lIC9GMSAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRlbnRzIDcgMCBSIC9NZWRpYUJveCBbIDAgMCA1OTUuMjc1NiA4NDEuODg5OCBdIC9QYXJlbnQgNiAwIFIgL1Jlc291cmNlcyA8PAovRm9udCAxIDAgUiAvUHJvY1NldCBbIC9QREYgL1RleHQgL0ltYWdlQiAvSW1hZ2VDIC9JbWFnZUkgXQo+PiAvUm90YXRlIDAgL1RyYW5zIDw8Cgo+PiAKICAvVHlwZSAvUGFnZQo+PgplbmRvYmoKNCAwIG9iago8PAovUGFnZU1vZGUgL1VzZU5vbmUgL1BhZ2VzIDYgMCBSIC9UeXBlIC9DYXRhbG9nCj4+CmVuZG9iago1IDAgb2JqCjw8Ci9BdXRob3IgKGFub255bW91cykgL0NyZWF0aW9uRGF0ZSAoRDoyMDI2MDkwODIzMzQ0Ny0wNScwMCcpIC9DcmVhdG9yIChSZXBvcnRMYWIgUERGIExpYnJhcnkgLSB3d3cucmVwb3J0bGFiLmNvbSkgL0tleXdvcmRzICgpIC9Nb2REYXRlIChEOjIwMjYwOTA4MjMzNDQ3LTA1JzAwJykgL1Byb2R1Y2VyIChSZXBvcnRMYWIgUERGIExpYnJhcnkgLSB3d3cucmVwb3J0bGFiLmNvbSkgCiAgL1N1YmplY3QgKHVuc3BlY2lmaWVkKSAvVGl0bGUgKHVudGl0bGVkKSAvVHJhcHBlZCAvRmFsc2UKPj4KZW5kb2JqCjYgMCBvYmoKPDwKL0NvdW50IDEgL0tpZHMgWyAzIDAgUiBdIC9UeXBlIC9QYWdlcwo+PgplbmRvYmoKNyAwIG9iago8PAovRmlsdGVyIFsgL0FTQ0lJODVEZWNvZGUgL0ZsYXRlRGVjb2RlIF0gL0xlbmd0aCAxMzQKPj4Kc3RyZWFtCkdhcEBFMGFiY18nTF9oZ2lmdEVZbzBePCJlM1lfZCJWcm5PX0pXYWEnImtvWWE6LDZoKmIwOkxwPlthQV86YkBnbGdbJXBDXjltXGFkXWxyP2V0SWEqP0o/aDY1JT0xQ14/azIzVSc4aHI3JUknbkFuTCwuVm5zOVwpLy1uJiZGMkVmKX4+ZW5kc3RyZWFtCmVuZG9iagp4cmVmCjAgOAowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwNzMgMDAwMDAgbiAKMDAwMDAwMDEwNCAwMDAwMCBuIAowMDAwMDAwMjExIDAwMDAwIG4gCjAwMDAwMDA0MTQgMDAwMDAgbiAKMDAwMDAwMDQ4MiAwMDAwMCBuIAowMDAwMDAwNzc4IDAwMDAwIG4gCjAwMDAwMDA4MzcgMDAwMDAgbiAKdHJhaWxlcgo8PAovSUQgCls8MGY5ZDc5MTE3MmZmODNlZmNiYmY0NTQzZWYzOGE1ZjM+PDBmOWQ3OTExNzJmZjgzZWZjYmJmNDU0M2VmMzhhNWYzPl0KJSBSZXBvcnRMYWIgZ2VuZXJhdGVkIFBERiBkb2N1bWVudCAtLSBkaWdlc3QgKGh0dHA6Ly93d3cucmVwb3J0bGFiLmNvbSkKCi9JbmZvIDUgMCBSCi9Sb290IDQgMCBSCi9TaXplIDgKPj4Kc3RhcnR4cmVmCjEwNjEKJSVFT0YK', 'base64')
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/auth/me') return json(route, { id: 'attorney', tenant_id: 'firm', full_name: 'Attorney', role: 'admin', capabilities: ['manage_matters', 'view_matters'], enabled_modules: ['matters', 'tasks', 'documents'], hidden_matter_panels: ['team', 'workflow'] })
    if (path === `/api/matters/${id}`) return json(route, { id, matter_name: 'Jane Doe divorce', status: 'open', stage: 'Prospective client', court: 'Cook County', assignments: [], key_dates: {} })
    if (path.endsWith(`/documents/${docId}/download`)) return route.fulfill({ contentType: 'application/pdf', body: pdf })
    if (path.endsWith('/document-folders')) return json(route, { items: [{ id: folderId, name: 'Intake', path: 'Intake', parent_id: null, depth: 0 }], root_document_count: 1 })
    if (path.endsWith('/documents/copy')) { copies.push(request.postDataJSON()); return json(route, { id: copies.at(-1).copy_id }) }
    if (path.endsWith('/documents')) return json(route, { items: [{ id: docId, filename: 'Attorney-prepared fee agreement.pdf', content_type: 'application/pdf', file_size: pdf.length, created_at: '2026-09-08T12:00:00Z' }] })
    if (path.endsWith('/dashboard-summary')) return json(route, { active_workers: [], upcoming_deadlines: [], open_tasks: 0, overdue_tasks: 0 })
    if (path.endsWith('/budget')) return json(route, {})
    if (path.endsWith('/cloud-files')) return json(route, { connected: false, files: [] })
    if (path.endsWith('/cloud-folder')) return json(route, null)
    if (path.includes('/templates')) return json(route, { items: [{ id: 'template', title: 'General intake form', is_active: true, format: 'pdf' }], total: 1 })
    if (path.endsWith('/document-tags') || path === '/api/tasks' || path === '/api/tasks/overdue') return json(route, { items: [], total: 0 })
    return json(route, [])
  })
  await page.goto(`/matters/${id}`)
  await dismissReleaseAnnouncement(page)
  await expect(page.getByRole('heading', { name: 'Jane Doe divorce' })).toBeVisible()
  await page.getByRole('button', { name: 'Customize view' }).click()
  await page.getByLabel('Court', { exact: true }).uncheck()
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByText('Cook County', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Documents', exact: true }).click()
  await page.getByRole('button', { name: 'Folder', exact: true }).click()
  await expect(page.getByLabel('Folder contents').getByRole('button', { name: 'Intake', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Attorney-prepared fee agreement.pdf' }).click()
  await expect(page.getByLabel('PDF page 1', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Close preview', exact: true }).click()
  await page.getByRole('button', { name: 'Copy to…' }).click()
  await page.getByLabel('Destination folder').selectOption(folderId)
  await page.getByRole('button', { name: 'Copy here' }).click()
  await expect.poll(() => copies.length).toBe(1)
  expect(copies[0]).toMatchObject({ document_id: docId, folder_id: folderId })
  await page.getByRole('button', { name: 'Attach template', exact: true }).click()
  await expect(page.getByText('General intake form', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Attach template' })).toHaveCount(0)
  if (process.env.MATTER_UX_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.MATTER_UX_ARTIFACT_DIR}/jane-doe-documents.png`, fullPage: true })
})
