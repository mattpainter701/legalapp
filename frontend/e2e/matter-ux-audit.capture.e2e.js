import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { dismissReleaseAnnouncement } from './release-announcement.js'

// Audit-only harness. Skipped unless E2E_AUDIT=true so it never runs in CI.
const enabled = process.env.E2E_AUDIT === 'true'
const outDir = path.resolve(process.env.E2E_AUDIT_OUT || 'test-results/matter-ux-audit')
const fixture = name => path.resolve('e2e/fixtures', name)
const mailbox = () => path.join(process.env.UPLOAD_DIR, 'onboarding-mailbox.jsonl')
const messages = () => {
  const file = mailbox()
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []
}
const labelled = (scope, name) => scope.getByLabel(new RegExp(`^\\s*${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}( \\*)?\\s*$`))
const json = async response => { expect(response.ok(), await response.text()).toBeTruthy(); return response.json() }

const obs = { capturedAt: new Date().toISOString(), steps: [], facts: {} }
const record = (step, data = {}) => { obs.steps.push({ step, at: new Date().toISOString(), ...data }) }

test('UX audit: matter onboarding journey capture', async ({ page, browser }) => {
  test.skip(!enabled, 'Audit capture only (set E2E_AUDIT=true)')
  test.setTimeout(240_000)
  fs.mkdirSync(outDir, { recursive: true })
  fs.rmSync(mailbox(), { force: true })
  const shot = async name => { try { await page.screenshot({ path: path.join(outDir, name), fullPage: true }) } catch (e) { record('screenshot-failed', { name, error: String(e).slice(0, 200) }) } }
  const paperShot = async (target, name) => { try { await target.screenshot({ path: path.join(outDir, name), fullPage: true }) } catch (e) { record('screenshot-failed', { name, error: String(e).slice(0, 200) }) } }
  let failure = null
  try {
  // ── Login ────────────────────────────────────────────────────────────────
  await page.goto('/login')
  await shot('01-login.png')
  await page.getByRole('button', { name: 'Sign in with email & password' }).click()
  await labelled(page, 'Email').fill('onboarding@playwright-e2e.example.com')
  await labelled(page, 'Password').fill(process.env.E2E_USER_PASSWORD || 'Playwright-Only-42!')
  await page.getByRole('button', { name: 'Sign In', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login$/)
  record('login', { url: page.url() })

  // ── Create matter + client ───────────────────────────────────────────────
  await page.goto('/matters')
  await dismissReleaseAnnouncement(page)
  await shot('02-matters-portfolio.png')
  await page.getByRole('button', { name: 'New Matter', exact: true }).click()
  await shot('03-new-matter-modal.png')
  await page.getByLabel('Matter Title').fill('Jane Doe divorce UX audit')
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
  obs.facts.matterId = matterId
  obs.facts.matterUrl = page.url()
  await expect(page.getByRole('heading', { name: 'Start this case' })).toBeVisible()
  await shot('04-matter-overview-start-case.png')
  record('matter-created', { matterId, startCaseCTA: await page.getByRole('button', { name: 'Send client paperwork' }).count() })

  // Every matter destination the shell offers on desktop.
  obs.facts.primaryTabs = await page.locator('.hidden.md\\:flex button').allInnerTexts().catch(() => [])

  // ── Prepare and send paperwork ───────────────────────────────────────────
  const upload = async (name, file) => json(await page.request.post(base + '/documents/upload', { multipart: { file: { name, mimeType: 'application/pdf', buffer: fs.readFileSync(fixture(file)) } } }))
  const fee = await upload('Fee agreement.pdf', 'fee-agreement.pdf')
  const form = await upload('General intake form.pdf', 'general-intake.pdf')
  // Visit Documents and preview the fee agreement: this is where a user would
  // upload, and returning to Overview remounts CaseSetupCard so it sees them.
  await page.getByRole('button', { name: 'Documents', exact: true }).click()
  await shot('04b-matter-documents.png')
  await page.getByRole('button', { name: 'Preview Fee agreement.pdf', exact: true }).click()
  await expect(labelled(page, 'PDF page 1')).toBeVisible()
  await page.getByRole('button', { name: 'Close preview', exact: true }).click()
  await page.getByRole('button', { name: 'Overview', exact: true }).click()
  await page.getByRole('button', { name: 'Send client paperwork', exact: true }).click()
  await page.getByRole('combobox', { name: 'Choose the fee agreement' }).selectOption(fee.id)
  await labelled(page, 'General intake form.pdf').check()
  await page.getByLabel('One question per line').fill('Describe your matter')
  await page.getByLabel('Requested client uploads — one per line').fill('Marriage certificate')
  await shot('05-paperwork-drawer-documents.png')
  await page.getByRole('button', { name: '3. Send' }).click()
  await shot('06-paperwork-drawer-send.png')
  await page.getByRole('button', { name: 'Send paperwork', exact: true }).click()
  await expect.poll(() => messages().length).toBe(1)
  await shot('07-paperwork-sent-strip.png')
  record('paperwork-sent', { emailSubject: messages()[0].subject, emailMentionsUploads: /Marriage certificate/.test(messages()[0].body), emailBodySnippet: messages()[0].body.slice(0, 400) })

  // ── Client signs the fee agreement ───────────────────────────────────────
  const link = messages()[0].body.match(/https?:[^ ]+token=[A-Za-z0-9_-]+/)[0]
  const clientContext = await browser.newContext({ baseURL: new URL(page.url()).origin })
  const clientPage = await clientContext.newPage()
  try {
    await clientPage.goto(link)
    await expect(clientPage).toHaveURL(/\/portal\/client\/matter$/)
    await paperShot(clientPage, '08-client-portal-overview.png')
    await paperShot(clientPage, '09-client-portal-checklist.png')
    await clientPage.getByRole('button', { name: 'Review and sign fee agreement', exact: true }).click()
    await paperShot(clientPage, '10-client-signing-card.png')
    const requests = await json(await clientPage.request.get('/api/portal/client/signatures'))
    const sign = async documentId => {
      const req = requests.find(item => item.document_id === documentId)
      const field = clientPage.locator(`[id="signature-${req.id}"]`)
      const card = field.locator('..')
      await field.fill('Jane Doe')
      await labelled(card, 'I have reviewed every page of this document.').check()
      await card.getByLabel(/I consent to use an electronic signature/).check()
      await card.getByRole('button', { name: 'Sign document', exact: true }).click()
      await expect(field).toHaveCount(0)
    }
    // Measure how fast the attorney learns about the signature, with the
    // Overview card already mounted (no manual reload).
    const signedAt = Date.now()
    await sign(fee.id)
    let observedMs = null
    try {
      await expect.poll(() => page.getByText(/Fee agreement signed/i).count(), { timeout: 60_000, intervals: [1000] }).toBeGreaterThan(0)
      observedMs = Date.now() - signedAt
    } catch { observedMs = -1 }
    obs.facts.signatureToInPageNoticeMs = observedMs
    obs.facts.notificationSurface = await page.evaluate(() => Array.from(document.querySelectorAll('[aria-label*="otification"], [aria-label*="lert"], [data-testid*="notification"]')).map(el => el.getAttribute('aria-label') || el.getAttribute('data-testid')).slice(0, 5)).catch(() => [])
    await shot('11-attorney-overview-after-sign.png')
    await expect.poll(() => messages().length).toBe(2)
    record('signed-and-notified', { observedMs, portalEmailSubject: messages()[1].subject, portalEmailSnippet: messages()[1].body.slice(0, 300) })

    const packet = await json(await page.request.get(base + '/intake'))
    obs.facts.signingFollowupDueAt = packet.signing_followup_due_at
    obs.facts.feeCompleted = packet.requirements.fee_agreement.completed
    obs.facts.followupDeltaHours = (new Date(packet.signing_followup_due_at) - new Date(packet.requirements.fee_agreement.completed_at)) / 3.6e6
    await sign(form.id)

    // Requested-records upload path.
    await clientPage.getByRole('tab', { name: 'Overview', exact: true }).click()
    await clientPage.getByLabel('Describe your matter', { exact: false }).fill('Divorce consultation')
    await clientPage.getByRole('button', { name: 'Submit completed questionnaire' }).click()
    await clientPage.screenshot({ path: path.join(outDir, '12-client-requested-docs.png'), fullPage: true }).catch(() => {})
    obs.facts.requestedUploadPrompt = await clientPage.getByText(/Marriage certificate|Upload completed document|Requested/i).count()
  } finally {
    await clientContext.close()
  }

  // ── Follow-up task on Tasks and Calendar ─────────────────────────────────
  await page.goto('/tasks')
  await expect(page.getByText('Fee agreement signed — follow up with client', { exact: true }).first()).toBeVisible()
  await shot('13-tasks-page.png')
  obs.facts.tasksBody = (await page.locator('body').innerText()).slice(0, 2000)
  await page.goto('/calendar')
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(2500)
  await shot('14-calendar-page.png')
  obs.facts.calendarHasTaskDue = await page.getByText(/Fee agreement|Task/i).count()
  obs.facts.calendarBody = (await page.locator('body').innerText()).slice(0, 1200)

  // ── Portal info + client interactions + history ──────────────────────────
  await page.goto(`/matters/${matterId}`)
  await page.getByRole('button', { name: 'Matter settings', exact: true }).click()
  await page.getByRole('button', { name: 'Client Portal', exact: true }).click()
  await shot('15-client-portal-tab.png')
  obs.facts.portalInviteControls = await page.getByRole('button', { name: /Send portal invite|Copy link|Revoke/i }).count()
  await page.getByRole('button', { name: 'Overview', exact: true }).click()
  await page.getByRole('button', { name: 'Activity', exact: true }).click()
  await page.waitForTimeout(1500)
  await shot('16-activity-history.png')
  obs.facts.activityText = (await page.locator('body').innerText()).slice(0, 1500)
  await page.getByRole('button', { name: 'Overview', exact: true }).click()
  await page.getByRole('button', { name: 'Email Client', exact: true }).click()
  await page.waitForTimeout(1000)
  await shot('17-email-client-composer.png')
  obs.facts.composerHasPortal = await page.getByText(/portal/i).count()

  fs.writeFileSync(path.join(outDir, 'observations.json'), JSON.stringify(obs, null, 2))
  record('complete', { outDir })
  } catch (e) {
    failure = e
    record('error', { message: String(e).slice(0, 600) })
  } finally {
    fs.writeFileSync(path.join(outDir, 'observations.json'), JSON.stringify(obs, null, 2))
  }
  if (failure) throw failure
})
