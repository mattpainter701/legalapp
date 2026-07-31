import React from 'react'
import { ArrowLeft, ArrowUp, Mail, ShieldCheck } from 'lucide-react'
import WellPledLogo from '../components/WellPledLogo'
import { Link } from 'react-router-dom'

const UPDATED = 'July 27, 2026'
const EMAIL = 'contact@perevagagroup.com'

const privacySections = [
  ['organization-role', 'Your organization’s role', [
    'WellPled workspaces are provided to firms and other organizations. When an organization provides your account, that organization administers the workspace, controls which modules and connected services are enabled, and may set workspace-specific retention and access policies. Its subscription agreement, data processing agreement, and privacy notices may provide additional or controlling terms for workspace data. Contact your organization’s administrator first if your request concerns information in its workspace.',
  ]],
  ['information-we-handle', 'Information we handle', [
    'The information we handle depends on how you and your organization configure and use the service. It may include:',
  ], [
    'Account and organization information, such as your name, business email address, firm or company name, role, authentication method, and account settings.',
    'Workspace content, such as caller intake records, contacts, matters, tasks, documents, communications, calendar information, billing records, notes, prompts, and AI-assisted outputs.',
    'Connected-service data made available through integrations authorized by your organization or an authorized user, including supported Microsoft, Google, Zoom, QuickBooks, Teams, email, calendar, file-storage, and payment workflows.',
    'Technical and security information, such as IP address, browser and device information, request timestamps, session and authentication events, audit events, and feature usage.',
    'Communications you send to us, including access requests, support questions, and feedback.',
  ]],
  ['how-we-use-information', 'How we use information', [
    'We use information to provide and support WellPled; authenticate users; enforce tenant, role, and module permissions; process workflows requested by authorized users; operate enabled integrations; administer subscriptions and payment features; maintain security; investigate errors or misuse; communicate about the service; and comply with legal and contractual obligations.',
  ]],
  ['ai-assisted-features', 'AI-assisted features', [
    'When an organization enables AI-assisted features, relevant prompts, documents, retrieved context, and outputs may be processed through the model provider configured for that workspace. Data handling can vary by provider, deployment, and tenant configuration. Your organization is responsible for approving the providers and workflows it enables. AI-assisted output requires professional review and should not be treated as verified legal advice.',
  ]],
  ['when-information-is-shared', 'When information is shared', [
    'We may make information available to infrastructure, model, integration, communications, support, monitoring, and payment providers as needed to operate features selected by your organization. We also send information to connected services when an authorized user requests a workflow, and may disclose information when required by law, to protect the service or its users, or in connection with a corporate transaction. Provider-specific processing is also governed by the provider’s terms and the configuration selected by your organization.',
  ]],
  ['retention-and-deletion', 'Retention and deletion', [
    'Retention depends on the type of information, the workspace configuration, the organization’s instructions, and applicable contractual or legal requirements. Workspace administrators may control available retention settings. Contact your organization’s administrator for the policy that applies to its workspace or to request deletion of workspace content. Information may remain where preservation is required for security, dispute resolution, legal compliance, or backup integrity.',
  ]],
  ['security', 'Security', [
    'WellPled uses tenant-scoped access controls and is designed to isolate firm workspaces. The service uses short-lived access sessions, rotating refresh state, and application encryption for stored integration credentials. Storage, model-provider, and connected-service protections also depend on the infrastructure, provider, and tenant configuration selected for the workspace. No method of storage or transmission is completely secure.',
  ]],
  ['choices-and-requests', 'Your choices and requests', [
    'You may be able to update account information through the service or your workspace administrator. Depending on applicable law, you may have rights to request access, correction, deletion, restriction, objection, or portability. If your request concerns an organization-managed workspace, contact that organization first. For questions about this policy or information handled directly by WellPled, email contact@perevagagroup.com. We may need to verify a request before responding.',
  ]],
  ['policy-changes', 'Changes to this policy', [
    'We may update this Privacy Policy as the service, providers, or legal requirements change. We will post the revised policy on this page and update the date above. Organization-specific notice obligations remain governed by the applicable subscription agreement or data processing agreement.',
  ]],
]

const termsSections = [
  ['organization-agreements', 'Organization agreements control', [
    'If you use WellPled through a firm or other organization, your access is also governed by that organization’s subscription agreement, order, data processing agreement, and applicable policies. Those organization-specific terms control if they conflict with these Terms. Commercial terms, enabled modules, support, retention, security commitments, and data processing obligations are established in those agreements, not on this public page.',
  ]],
  ['authorized-use', 'Authorized use', [
    'You may use WellPled only if you are authorized by the organization responsible for the workspace and only for that organization’s legitimate professional activities. You must provide accurate account information, protect your credentials, use only your own account, and promptly notify your administrator of suspected unauthorized access. Workspace administrators control user access, roles, modules, and connected services.',
  ]],
  ['the-service', 'The service', [
    'WellPled can support caller intake, tasks, matters, documents, billing, cloud integrations, and source-aware legal research. Available features vary by subscription, tenant configuration, provider readiness, and controlled onboarding. Some features or integrations may be disabled or unavailable. Descriptions on the public website do not promise that every module, provider, or workflow is included in your organization’s service.',
  ]],
  ['professional-responsibility', 'Professional responsibility and AI-assisted output', [
    'WellPled assists legal professionals; it does not replace professional judgment. You are responsible for reviewing documents, deadlines, calculations, citations, research results, and other output before relying on or sharing them. AI-assisted content may be incomplete, inaccurate, or outdated. A citation, source label, or confidence cue is a review aid, not a guarantee that a source is accurate, complete, or still good law. WellPled does not provide legal advice and does not create an attorney-client relationship.',
  ]],
  ['workspace-content', 'Workspace content', [
    'You and your organization are responsible for the legality, accuracy, and appropriateness of information submitted to the service and for having the rights and permissions needed to process it. You remain responsible for professional duties concerning confidentiality, privilege, client instructions, records management, and use of sensitive information. Organization-specific rights and obligations concerning workspace content are governed by the applicable subscription agreement and data processing agreement.',
  ]],
  ['connected-services', 'Connected services', [
    'WellPled may connect with third-party services selected by your organization, including supported Microsoft, Google, Zoom, QuickBooks, Teams, email, file-storage, model-provider, and payment services. Your use of a connected service is also subject to that provider’s terms and permissions. WellPled is not responsible for a third-party service’s availability, changes, or independent handling of information. Disconnect integrations you no longer authorize.',
  ]],
  ['acceptable-use', 'Acceptable use', ['You must not:'], [
    'Access a workspace, account, matter, document, or integration without authorization.',
    'Use the service to violate law, professional duties, court rules, contractual obligations, privacy rights, or intellectual property rights.',
    'Upload malicious code; probe or disrupt security; bypass access, module, usage, or billing controls; or interfere with other users.',
    'Misrepresent AI-assisted output as independently verified, or rely on the service as a substitute for required professional review.',
    'Use automated means to scrape or extract the service except through an interface expressly authorized for your organization.',
    'Reverse engineer or attempt to derive non-public source code except where applicable law does not permit that restriction.',
  ]],
  ['ownership-and-feedback', 'Ownership and feedback', [
    'WellPled and its licensors retain rights in the service, software, interface, and related materials. These Terms do not transfer ownership of workspace content to WellPled. If you provide product feedback, you allow us to use it to evaluate and improve the service without an obligation to compensate you. Any broader license or data-use terms are governed by the applicable organization agreement.',
  ]],
  ['availability-and-changes', 'Availability and changes', [
    'We may maintain, update, secure, or change the public website and service. Access may be limited when needed to address security, legal, provider, or operational issues. Service levels, support commitments, notice requirements, and remedies—if any—are governed by the applicable organization agreement.',
  ]],
  ['suspension-and-termination', 'Suspension and termination', [
    'Your organization may remove your access. We may restrict access when reasonably necessary to protect the service, respond to suspected misuse, comply with law, or enforce applicable agreements. Subscription termination, account closure, data return, export, and deletion are governed by the organization’s agreements and workspace policy.',
  ]],
  ['disclaimers', 'Disclaimers', [
    'Except for express commitments in an applicable organization agreement, the public website and service are provided on an “as available” basis to the extent permitted by law. We do not warrant that AI-assisted output, third-party content, citations, integrations, or connected services will be error-free, complete, current, or continuously available. Nothing in these Terms excludes a warranty or right that cannot lawfully be excluded.',
  ]],
  ['liability', 'Liability', [
    'Liability between WellPled and an organization is governed by the applicable subscription agreement. To the extent these Terms apply independently and to the extent permitted by law, WellPled is not liable for indirect, incidental, special, consequential, or punitive damages, or for losses caused by reliance on unreviewed output, unauthorized use, or third-party services. Nothing in these Terms limits liability that cannot lawfully be limited.',
  ]],
  ['terms-changes', 'Changes to these Terms', [
    'We may update these Terms to reflect changes to the service or legal requirements. We will post the revised Terms on this page and update the date above. Changes to an organization’s controlling subscription or data processing terms will be handled under those agreements.',
  ]],
]

const notices = {
  privacy: {
    eyebrow: 'How information is handled',
    title: 'Privacy Policy',
    intro: 'This Privacy Policy explains how WellPled handles personal information when you visit our public website, request access, or use a WellPled workspace. WellPled is a multi-tenant, AI-assisted legal operations platform for law firms and legal professionals.',
    sections: privacySections,
    otherPath: '/terms',
    otherLabel: 'Terms of Use',
  },
  terms: {
    eyebrow: 'Rules for using WellPled',
    title: 'Terms of Use',
    intro: 'These Terms of Use apply to the WellPled public website and to your access to a WellPled workspace. WellPled is a multi-tenant, AI-assisted legal operations platform for law firms and legal professionals.',
    sections: termsSections,
    otherPath: '/privacy',
    otherLabel: 'Privacy Policy',
  },
}

function LegalSection({ section, number }) {
  const [id, title, paragraphs, items] = section
  return (
    <section id={id} aria-labelledby={id + '-title'} className='scroll-mt-24 border-t border-brand-line pt-8 first:border-0 first:pt-0'>
      <div className='flex items-start gap-4'>
        <span aria-hidden='true' className='mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-brand-line bg-brand-bg-soft font-sans text-xs font-bold text-brand-accent-2'>
          {String(number).padStart(2, '0')}
        </span>
        <div>
          <h2 id={id + '-title'} className='font-serif text-2xl font-bold tracking-tight'>{title}</h2>
          <div className='mt-3 space-y-4 font-sans text-[15px] leading-7 text-brand-ink-2'>
            {paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
            {items && (
              <ul className='space-y-3 pl-1'>
                {items.map((item) => (
                  <li key={item} className='flex gap-3'>
                    <span aria-hidden='true' className='mt-[0.68rem] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-accent-2' />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

export default function LegalNoticePage({ type }) {
  const notice = notices[type] || notices.terms

  React.useEffect(() => {
    document.documentElement.scrollTop = 0
    document.body.scrollTop = 0
  }, [type])

  return (
    <div id='top' className='min-h-screen bg-brand-bg text-brand-ink'>
      <a href='#main-content' className='sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-brand-ink focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white'>Skip to main content</a>

      <header className='sticky top-0 z-40 border-b border-brand-line bg-brand-bg/90 backdrop-blur'>
        <div className='mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6'>
          <Link to='/' aria-label='WellPled home' className='rounded-lg'>
            <WellPledLogo compact />
          </Link>
          <div className='flex items-center gap-2 sm:gap-4'>
            <Link to={notice.otherPath} aria-label={notice.otherLabel} className='inline-flex min-h-11 items-center px-1 font-sans text-sm font-semibold text-brand-ink-2 hover:text-brand-ink sm:px-2'>
              <span className='hidden sm:inline'>{notice.otherLabel}</span>
              <span className='sm:hidden'>{type === 'privacy' ? 'Terms' : 'Privacy'}</span>
            </Link>
            <Link to='/' aria-label='Back to home' className='inline-flex min-h-11 items-center gap-2 rounded-xl bg-brand-ink px-3.5 py-2 font-sans text-sm font-semibold text-white shadow-sm hover:bg-brand-ink-2'>
              <ArrowLeft size={16} aria-hidden='true' /><span className='hidden sm:inline'>Back to home</span><span className='sm:hidden'>Home</span>
            </Link>
          </div>
        </div>
      </header>

      <main id='main-content' tabIndex='-1'>
        <section className='border-b border-brand-line bg-brand-bg-soft/55'>
          <div className='mx-auto max-w-6xl px-5 py-12 sm:px-6 md:py-16'>
            <span className='inline-flex items-center gap-2 rounded-full border border-brand-line bg-brand-surface px-3 py-1.5 font-sans text-xs font-bold uppercase tracking-[0.14em] text-brand-accent-2'><ShieldCheck size={14} aria-hidden='true' /> {notice.eyebrow}</span>
            <h1 className='mt-5 max-w-3xl font-serif text-4xl font-bold tracking-tight sm:text-5xl'>{notice.title}</h1>
            <p className='mt-5 max-w-3xl font-sans text-base leading-8 text-brand-ink-2 sm:text-[17px]'>{notice.intro}</p>
            <p className='mt-5 font-sans text-sm font-semibold text-brand-muted'>Last updated {UPDATED}</p>
          </div>
        </section>

        <div className='mx-auto grid max-w-6xl gap-10 px-5 py-10 sm:px-6 md:py-14 lg:grid-cols-[17rem_minmax(0,1fr)] lg:gap-14'>
          <aside>
            <nav aria-label={notice.title + ' table of contents'} className='rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm lg:sticky lg:top-24'>
              <p className='font-serif text-lg font-bold'>On this page</p>
              <ol className='mt-4 space-y-1'>
                {notice.sections.map(([id, title], index) => (
                  <li key={id}>
                    <a href={'#' + id} className='flex min-h-10 items-start gap-2 rounded-lg px-2 py-2 font-sans text-[13px] leading-5 text-brand-ink-2 hover:bg-brand-bg-soft hover:text-brand-ink'>
                      <span aria-hidden='true' className='font-semibold text-brand-muted'>{String(index + 1).padStart(2, '0')}</span><span>{title}</span>
                    </a>
                  </li>
                ))}
                <li><a href='#contact' className='flex min-h-10 items-start gap-2 rounded-lg px-2 py-2 font-sans text-[13px] leading-5 text-brand-ink-2 hover:bg-brand-bg-soft hover:text-brand-ink'><span aria-hidden='true' className='font-semibold text-brand-muted'>{String(notice.sections.length + 1).padStart(2, '0')}</span><span>Contact</span></a></li>
              </ol>
            </nav>
          </aside>
          <article className='min-w-0 rounded-2xl border border-brand-line bg-brand-surface px-5 py-8 shadow-sm sm:px-8 md:py-10'>
            <div className='space-y-9'>
              {notice.sections.map((section, index) => <LegalSection key={section[0]} section={section} number={index + 1} />)}
              <section id='contact' aria-labelledby='contact-title' className='scroll-mt-24 border-t border-brand-line pt-8'>
                <div className='rounded-2xl bg-brand-ink px-6 py-7 text-white sm:px-8'>
                  <div className='flex items-start gap-4'>
                    <span className='flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10'><Mail size={19} aria-hidden='true' /></span>
                    <div>
                      <h2 id='contact-title' className='font-serif text-2xl font-bold'>Contact</h2>
                      <p className='mt-2 font-sans text-sm leading-6 text-white/75'>For questions about this {type === 'privacy' ? 'Privacy Policy' : 'Terms of Use'}, email <a href={'mailto:' + EMAIL} className='font-semibold text-white underline underline-offset-4'>{EMAIL}</a>.</p>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </article>
        </div>
      </main>

      <footer className='border-t border-brand-line bg-brand-surface'>
        <div className='mx-auto flex max-w-6xl flex-col gap-5 px-5 py-8 sm:px-6 md:flex-row md:items-center md:justify-between'>
          <WellPledLogo compact />
          <nav aria-label='Legal footer' className='flex flex-wrap items-center gap-x-5 font-sans text-sm text-brand-muted'>
            <Link to='/privacy' aria-current={type === 'privacy' ? 'page' : undefined} className='inline-flex min-h-11 items-center hover:text-brand-ink'>Privacy</Link>
            <Link to='/terms' aria-current={type === 'terms' ? 'page' : undefined} className='inline-flex min-h-11 items-center hover:text-brand-ink'>Terms</Link>
            <a href='#top' className='inline-flex min-h-11 items-center gap-1.5 hover:text-brand-ink'>Back to top <ArrowUp size={14} aria-hidden='true' /></a>
          </nav>
        </div>
      </footer>
    </div>
  )
}
