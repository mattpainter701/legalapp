import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { dismissReleaseAnnouncement } from './release-announcement.js'

const enabled = process.env.E2E_ONBOARDING_ACCEPTANCE === 'true'
const fixture = name => path.resolve('e2e/fixtures', name)
const mailbox = () => path.join(process.env.UPLOAD_DIR, 'onboarding-mailbox.jsonl')
const messages = () => {
  const file = mailbox()
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []
}
// Required fields render their label as "Name *", and a label wrapping its
// own control keeps the space before the text. Regex label matching does not
// normalize whitespace, so an exact match has to allow for both. Selects are
// addressed by role instead: a wrapping label takes in its option text, while
// the accessible name the client actually hears stays just the field name.
const labelled = (scope, name) => scope.getByLabel(new RegExp(`^\\s*${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}( \\*)?\\s*$`))
const json = async response => { expect(response.ok(), await response.text()).toBeTruthy(); return response.json() }

test('Jane Doe: create matter, review fee, send paperwork, sign, unlock portal, follow up and receive records', async ({ page, browser }) => {
  test.skip(!enabled, 'Requires disposable PostgreSQL onboarding acceptance host')
  test.setTimeout(180_000)
  // The mailbox is a plain local file, so a rerun would otherwise inherit the
  // previous run's messages and hide a packet this one never sent.
  fs.rmSync(mailbox(), { force: true })
  await page.goto('/login')
  await page.getByRole('button', { name: 'Sign in with email & password' }).click()
  await labelled(page, 'Email').fill('onboarding@playwright-e2e.example.com')
  await labelled(page, 'Password').fill(process.env.E2E_USER_PASSWORD || 'Playwright-Only-42!')
  await page.getByRole('button', { name: 'Sign In', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login$/)
  await page.goto('/matters')
  await dismissReleaseAnnouncement(page)
  await page.getByRole('button', { name: 'New Matter', exact: true }).click()
  await page.getByLabel('Matter Title').fill('Jane Doe divorce acceptance')
  await page.getByRole('button', { name: '+ Create new contact', exact: true }).click()
  await labelled(page, 'First Name').fill('Jane')
  await labelled(page, 'Last Name').fill('Doe')
  await labelled(page, 'Email').fill('jane.onboarding@example.com')
  await page.getByRole('button', { name: 'Create & Select Contact', exact: true }).click()
  await expect(labelled(page, 'Client')).not.toHaveValue('')
  await page.getByRole('button', { name: 'Open Matter', exact: true }).click()
  await expect(page).toHaveURL(/\/matters\/[a-f0-9-]+$/)
  const matterId = new URL(page.url()).pathname.split('/').at(-1)
  const base = `/api/matters/${matterId}`
  expect((await page.request.get(base + '/intake')).status()).toBe(404)
  expect(messages()).toHaveLength(0)
  const upload = async (name, file) => json(await page.request.post(base + '/documents/upload', { multipart: { file: { name, mimeType: 'application/pdf', buffer: fs.readFileSync(fixture(file)) } } }))
  const fee = await upload('Fee agreement.pdf', 'fee-agreement.pdf')
  const form = await upload('General intake form.pdf', 'general-intake.pdf')
  await page.getByRole('button', { name: 'Documents', exact: true }).click()
  await page.getByRole('button', { name: 'Preview Fee agreement.pdf', exact: true }).click()
  await expect(labelled(page, 'PDF page 1')).toBeVisible()
  await page.getByRole('button', { name: 'Close preview', exact: true }).click()
  // Paperwork is started from the matter's Overview now, not the Documents
  // tab: the case-setup card owns it, and the drawer walks documents,
  // deadlines, then delivery.
  await page.getByRole('button', { name: 'Overview', exact: true }).click()
  await page.getByRole('button', { name: 'Send client paperwork', exact: true }).click()
  await page.getByRole('combobox', { name: 'From matter documents' }).selectOption(fee.id)
  await labelled(page, 'General intake form.pdf').check()
  await page.getByLabel('One question per line').fill('Describe your matter')
  await page.getByLabel('Requested client uploads — one per line').fill('Marriage certificate')
  await page.getByRole('button', { name: '3. Send' }).click()
  await expect(labelled(page, 'Client email')).toHaveValue('jane.onboarding@example.com')
  await page.getByRole('button', { name: 'Send paperwork', exact: true }).click()
  await expect.poll(() => messages().length).toBe(1)
  const initial = messages()[0]
  expect(initial.body).toContain('General intake form')
  const link = initial.body.match(/https?:[^ ]+token=[A-Za-z0-9_-]+/)[0]
  const clientContext = await browser.newContext({ baseURL: new URL(page.url()).origin })
  const clientPage = await clientContext.newPage()
  try {
    await clientPage.goto(link)
    await expect(clientPage).toHaveURL(/\/portal\/client\/matter$/)
    await expect(clientPage.getByRole('tab', { name: 'Messages', exact: true })).toHaveCount(0)
    expect((await clientPage.request.get('/api/portal/client/messages')).status()).toBe(403)
    await clientPage.getByRole('button', { name: 'Review and sign fee agreement', exact: true }).click()
    const requests = await json(await clientPage.request.get('/api/portal/client/signatures'))
    expect(requests).toHaveLength(2)
    const sign = async documentId => {
      const req = requests.find(item => item.document_id === documentId)
      const field = clientPage.locator(`[id="signature-${req.id}"]`)
      const card = field.locator('..')
      await expect(labelled(card, 'PDF page 1')).toBeVisible()
      await field.fill('Jane Doe')
      await labelled(card, 'I have reviewed every page of this document.').check()
      await card.getByLabel(/I consent to use an electronic signature/).check()
      await card.getByRole('button', { name: 'Sign document', exact: true }).click()
      await expect(field).toHaveCount(0)
    }
    await sign(fee.id)
    await expect.poll(() => messages().length).toBe(2)
    expect(messages()[1].body).toContain('portal')
    const packet = await json(await page.request.get(base + '/intake'))
    expect(packet.requirements.fee_agreement.completed).toBe(true)
    expect(packet.requirements.questionnaire.completed).toBe(false)
    expect(packet.requirements[`document_${form.id.replaceAll('-', '')}`].completed).toBe(false)
    expect(new Date(packet.signing_followup_due_at) - new Date(packet.requirements.fee_agreement.completed_at)).toBe(24 * 60 * 60 * 1000)
    await expect(clientPage.getByRole('tab', { name: 'Messages', exact: true })).toBeVisible()
    await sign(form.id)
    await clientPage.getByRole('tab', { name: 'Overview', exact: true }).click()
    await clientPage.getByLabel('Describe your matter', { exact: false }).fill('Divorce consultation')
    await clientPage.getByRole('button', { name: 'Submit completed questionnaire' }).click()
    await expect(clientPage.getByText('Questionnaire: Complete', { exact: true })).toBeVisible()
    await labelled(clientPage, 'Upload completed document').setInputFiles(fixture('general-intake.pdf'))
    await expect(clientPage.getByText('Submitted — awaiting staff review', { exact: true })).toBeVisible()
    const submitted = await json(await page.request.get(base + '/intake'))
    expect(submitted.requirements.upload_1.completed).toBe(false)
    const docId = submitted.requirements.upload_1.submitted_document_id
    await page.reload()
    // The strip is on the Overview, which is where a reload lands, and its
    // summary counts what is waiting on staff.
    await page.getByText(/^\s*Review received documents/).click()
    await page.getByRole('combobox', { name: 'Requirement', exact: true }).selectOption('upload_1')
    await page.getByRole('combobox', { name: 'Received document', exact: true }).selectOption(docId)
    await labelled(page, 'Verification note').fill('Reviewed the requested record')
    await page.getByRole('button', { name: 'Confirm document is complete', exact: true }).click()
    await expect(page.getByText(/Contact the client to schedule by/)).toBeVisible()
    await page.goto('/tasks')
    // Exactly one task per packet is pinned by the backend acceptance test; a
    // reused disposable database also holds earlier runs', so match the first.
    await expect(page.getByText('Fee agreement signed — follow up with client', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('Intake complete — contact client to schedule initial meeting', { exact: true }).first()).toBeVisible()
  } finally { await clientContext.close() }
})
