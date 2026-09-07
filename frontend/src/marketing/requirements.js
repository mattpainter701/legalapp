/**
 * Reviewed public platform-requirements and integration matrix.
 *
 * A firm cannot evaluate LawHand from a capability list alone: someone has to
 * know which administrator must consent, what that consent grants, and what the
 * onboarding actually involves. This module is the customer-facing half of
 * docs/integrations-setup.md — the prerequisites and the consent boundary, with
 * no operator procedure, environment variable, hostname, or runbook step.
 *
 * Scope strings come from frontend/src/marketing/integration-scopes.json, which
 * backend/tests/test_public_integration_scopes.py asserts against the constants
 * in backend/app/routers/integrations.py. Narrowing a scope fails that test
 * rather than leaving a stale claim on the public site.
 */

import scopeMatrix from './integration-scopes.json'

export const REQUIREMENTS_REVIEW = Object.freeze({
  owner: 'Product & Commercial',
  reviewedAt: '2026-09-07',
  nextReviewAt: '2026-12-07',
})

export const INTEGRATION_SCOPES = Object.freeze(scopeMatrix.providers)

/** Platform prerequisites. Nothing here should require a desktop install. */
export const PLATFORM_REQUIREMENTS = Object.freeze([
  Object.freeze({
    id: 'browser',
    title: 'A current desktop browser',
    detail:
      'LawHand runs in the browser. Use a current release of Chrome, Edge, Firefox, or Safari with JavaScript and cookies enabled. There is no desktop client to install and no server for the firm to run.',
  }),
  Object.freeze({
    id: 'accounts',
    title: 'A work email address per licensed user',
    detail:
      'Each user signs in with their own account. Shared logins break the per-user audit trail that matter, document, and billing records depend on.',
  }),
  Object.freeze({
    id: 'network',
    title: 'Outbound HTTPS',
    detail:
      'The workspace needs standard outbound HTTPS. Firms that filter egress should allow the LawHand application domain and the domains of any cloud provider they choose to connect.',
  }),
  Object.freeze({
    id: 'optional-surfaces',
    title: 'Optional Microsoft surfaces',
    detail:
      'A Microsoft Teams app and an Office add-in are separate, opt-in surfaces. Neither is required to use LawHand, and both stay disabled until a firm asks for them.',
  }),
])

/**
 * Per-provider onboarding facts. `adminConsent` answers the question a buyer
 * actually asks: does my IT administrator have to be involved, and what are
 * they agreeing to?
 */
export const CLOUD_PROVIDERS = Object.freeze([
  Object.freeze({
    id: 'microsoft',
    name: 'Microsoft 365',
    identity: 'Microsoft Entra ID',
    adminRole: 'Global Administrator (or a role that can grant tenant-wide admin consent)',
    connects: Object.freeze([
      'Directory read, to match LawHand users to firm accounts',
      'Outlook mail read and send for matter correspondence',
      'OneDrive and SharePoint file read/write for matter documents',
      'Calendar read/write for deadlines and matter events',
    ]),
    perUserAlternative:
      'A firm that does not want tenant-wide consent can have each user connect individually. Per-user consent drops the directory scope, so user matching becomes manual.',
    optional: 'Microsoft Teams scopes are added only when a firm explicitly opts in.',
  }),
  Object.freeze({
    id: 'google',
    name: 'Google Workspace',
    identity: 'Google Workspace identity',
    adminRole: 'Super Admin (or a delegated admin who can approve the OAuth client)',
    connects: Object.freeze([
      'Directory read (read-only) to match LawHand users to firm accounts',
      'Gmail read and send for matter correspondence',
      'Google Drive file access for matter documents',
      'Calendar access for deadlines and matter events',
    ]),
    perUserAlternative:
      'Individual users can connect their own account instead. Per-user consent drops the directory scope, so user matching becomes manual.',
    optional: 'There is no Workspace equivalent of the Teams opt-in.',
  }),
])

/** The customer-visible sequence. Operator proof steps stay in the runbooks. */
export const ONBOARDING_STEPS = Object.freeze([
  Object.freeze({
    id: 'scope',
    title: 'Agree the rollout scope',
    detail:
      'Decide which modules the firm is licensing and which integrations it actually needs. Unused integrations stay disabled rather than consented "just in case".',
  }),
  Object.freeze({
    id: 'consent',
    title: 'Grant consent',
    detail:
      'An administrator signs in and approves the consent screen for the chosen provider. This is the step that needs Entra Global Administrator or Google Super Admin rights.',
  }),
  Object.freeze({
    id: 'verify',
    title: 'Verify the connection',
    detail:
      'LawHand audits the scopes actually granted and reports anything missing, so a partial consent is visible immediately instead of failing later in a workflow.',
  }),
  Object.freeze({
    id: 'bind',
    title: 'Bind storage and run a live operation',
    detail:
      'Where the firm routes matter files to SharePoint or Drive, the destination site, library, and folder are selected and confirmed against a real read and write before the integration is treated as ready.',
  }),
  Object.freeze({
    id: 'record',
    title: 'Record and test revocation',
    detail:
      'The granted scopes, connection time, and the operation tested are recorded for the firm, and disconnect is exercised so revocation is known to work before go-live rather than during an incident.',
  }),
])

/** Limits a buyer should know before signing, not after. */
export const KNOWN_BOUNDARIES = Object.freeze([
  Object.freeze({
    id: 'scope-breadth',
    title: 'Current consent is broader than some firms will want',
    detail:
      'Tenant-wide consent today requests one bundle covering directory, mail, files, sites, and calendar, because the full platform uses all of them. A firm licensing only part of the platform should disable the modules it is not using, and can connect per user instead. Splitting these into narrower, per-module consent — including selected-site SharePoint access — is planned work, not a current capability.',
  }),
  Object.freeze({
    id: 'sharepoint',
    title: 'SharePoint and Drive bindings are verified, not assumed',
    detail:
      'A matter file saved locally in LawHand is not evidence that it reached the firm’s SharePoint or Drive destination. The provider object is checked on write, and a binding is not treated as working until that check passes.',
  }),
  Object.freeze({
    id: 'zoom-phone',
    title: 'Zoom Phone intake is a separate application',
    detail:
      'Call intake uses a customer-owned Zoom application, distinct from Zoom meeting integration. Working meeting OAuth is not evidence that call intake works.',
  }),
  Object.freeze({
    id: 'quickbooks',
    title: 'QuickBooks Online needs its own environment choice',
    detail:
      'The QuickBooks connection is tied to a specific sandbox or production company and is proved against that company before a firm relies on it.',
  }),
])

/** Flatten a provider's scope lists for rendering. */
export function providerScopes(providerId) {
  const entry = INTEGRATION_SCOPES[providerId]
  if (!entry) return { admin: [], user: [], teamsOptIn: [] }
  return entry
}
