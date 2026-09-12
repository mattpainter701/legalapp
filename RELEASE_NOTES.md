# Customer release notes

Plain-language highlights for people using LawHand. For implementation,
security, and migration details, see the [technical changelog](CHANGELOG.md).

<!-- Generated from backend/app/release_notes.json. Do not edit by hand. -->

## 2026.09.12.4 — Sessions that end when you end them

Released September 12, 2026.

Resetting your password now signs out everyone using your account and disconnects your AI assistants, and the app tells you which ones to reconnect. Unused sessions sign out after 12 hours.

- **A password reset signs out everyone.** Resetting your password now ends every session on your account, on every device. Before, it changed the password and left anyone already signed in where they were.
- **It disconnects your assistants too.** A reset now cuts off Claude, ChatGPT, Codex and any other connected assistant. The app then names each one and shows you how to reconnect it.
- **Sign out your other devices.** A new control on your profile ends every other session on your account. The device you are using stays signed in.
- **Unused sessions end on their own.** A session left alone for 12 hours signs out, and none stays signed in beyond 30 days. You will be asked to sign in more often than before.

## 2026.09.12.3 — Matter folders carry the matter number; automations stay in scope

Released September 12, 2026.

New matter folders are named with the matter number instead of an internal ID. A workflow rule scoped to one kind of work no longer fires on every matter in its practice. An intake answer too long to store now reaches staff.

- **Cloud folders are named with the matter number.** A new matter's folder reads Smith (CYBE0012) rather than an internal ID. Folders made before this keep their names and stay bound, and the audit recognises both.
- **Automation rules stay inside the scope you set.** A rule scoped to Adoption no longer fires on divorces, or Chapter 7 on Chapter 13. A rule scoped to a whole practice, like Family Law, still covers every matter in it.
- **An answer too long to store is reported, not dropped.** When a client's answer is longer than the field holds, the review task names it and says how long it is, instead of leaving the field quietly empty.

## 2026.09.12.2 — Bills you can correct, send, and settle from trust

Released September 12, 2026.

Draft invoices can be corrected, discounted and emailed to the client, and client funds on retainer settle a bill in one step. Timers ask what you worked on, and firm financial reports are now partner-only.

- **Correct a bill before the client sees it.** Reword, re-price or remove a charge on a draft, and add a discount or flat fee. Removing a charge returns the work to your unbilled queue.
- **Email an invoice with its PDF.** Send the bill to your client from the invoice screen. If delivery fails the invoice stays a draft, so it is never recorded as sent.
- **Settle a bill from client funds.** Apply a retainer to an invoice in one step: the retainer is drawn down and the payment recorded together, with a low balance flagged.
- **Timers that produce a billable entry.** Describe the work on the running timer and it lands ready to bill. Enter time as 1.5, 1:30 or 90m, in your firm's own billing increment.
- **Financial reports are partner-only.** Receivables, realization and work in progress now need billing access. Aging separates invoices not yet due from those overdue.
- **Editing a time entry or expense date works again.** Saving an edited time entry or expense failed whenever the date was included, which was every save. It now saves.

## 2026.09.12.1 — Sign and fill inside the document

Released September 12, 2026.

Clients fill form fields and sign on the document's own signature line in the portal, or upload a signed copy for your review. Paperwork sends your own fee agreement, intake form and questionnaire PDFs. Dropbox Sign is retired.

- **Sign on the signature line.** The portal renders your PDF, places inputs on its form fields, and puts the client's adopted signature on the signature line. PDF fields and printed signature lines are detected.
- **Or download, sign and upload.** A client who prefers paper downloads the form, completes it, and uploads the signed copy. It waits in the E-Signature queue for you to accept or send back.
- **A filed signed copy, not just a certificate.** Every completed request files the filled, signed, flattened PDF to the matter beside the evidence certificate.
- **Paperwork uses your own forms.** Send client paperwork lists the fee agreement, intake form and questionnaire as PDFs you supply, each with a client-signs toggle; requested records are an optional section.
- **Storage outages no longer fail signing.** If the firm's cloud storage is down when a client signs, the signature is kept, the client sees a confirmation, and filing retries automatically while staff see the problem.
- **One signing provider.** The Dropbox Sign option is removed. LawHand's own portal signing is the only provider, so there is nothing to pick or configure.

## 2026.09.11.12 — One practice resolver behind routing

Released September 11, 2026.

Plugin suggestions and workflow automations resolve matter labels through one shared alias-aware resolver, so “Dissolution of Marriage” routes to family everywhere. Matters show the resolved practice so staff can correct mis-resolved ones.

- **One resolver behind routing.** Plugin suggestions and workflow automations resolve matter labels through the same alias table the intake pack uses, so “Dissolution of Marriage” is family everywhere.
- **Plugin suggestions understand practice aliases.** A matter whose text never mentions a plugin's keywords still gets the right add-on when its practice resolves; existing substring matches keep working, so tenants do not regress.
- **The matter record shows its resolved practice.** Matter detail now carries the resolved practice slug and label, so staff can see how a matter was read and correct one that resolved wrong.

## 2026.09.11.15 — Fee agreements fill themselves — drafts can't be approved half-empty

Released September 11, 2026.

The retainer, its replenishment threshold, the contingency percentage, and the venue in your fee agreement now fill from the matter and its retainer record; a template whose required placeholders nothing can fill can't be approved.

- **Retainer and contingency terms fill from the matter.** The retainer amount, the balance that triggers replenishment, the contingency percentage, and the venue now fill from the matter record and its current retainer.
- **Matters record their venue.** The matter record gains a venue field, set the same way as jurisdiction, so every document that names a forum fills from one place.
- **Half-empty templates can't be approved.** Approving a template now checks that every required placeholder has a record or a default behind it, and jurisdiction terms must name their jurisdiction.

## 2026.09.11.14 — Preview the message before you send it

Released September 11, 2026.

Client paperwork now goes out as a branded email with the firm's name and contact details and a clear portal button, with a separate short text, and the Send step previews both before anything is sent.

- **See the message before you send it.** The Send step shows the exact branded email and short text the client will receive, rendered by the same code that delivers them.
- **The email looks like your firm.** Paperwork now carries your firm's name and contact details and an Open Secure Client Portal button instead of one plain paragraph.
- **Email and text are separate messages.** The full checklist stays in the email; the text is a short line with the link, so the message is readable on any phone.
- **One link, described correctly.** The message no longer promises a second portal link: the invitation link is the portal, and signing the fee agreement unlocks the rest.

## 2026.09.11.13 — Intake answers apply to the matter after review

Released September 11, 2026.

Questionnaire answers propose updates to the contact and matter record (address, phone, case number, court, jurisdiction) for staff to accept. Conflict-of-interest names are checked against firm contacts; nothing changes without approval.

- **Review questionnaire updates instead of retyping them.** Answers that map to the client contact or the matter — address, phone, case number, court, jurisdiction — become changes staff accept or reject, nothing retyped.
- **Conflicts of interest checked automatically.** Names from the conflict section are checked against the firm's contacts and matters when the form is submitted, and the result is attached to the review task.
- **Nothing changes without staff approval.** Every proposed update waits for a staff member's decision, and a change is refused for re-review if the record moved after the proposal was made.

## 2026.09.11.16 — Send any of the three standard pieces

Released September 11, 2026.

Start this case now lists the fee agreement, questionnaire, and client intake form together, fills the questionnaire with the matter type's own questions, and can send any subset.

- **The three common pieces, side by side.** The fee agreement, client questionnaire, and client intake form are each listed and selectable, so a firm can send one, two, or all three.
- **A fee agreement is now optional.** Send only the questionnaire or requested uploads when no agreement is needed. Signing still opens the portal when an agreement goes out.
- **Questions match the matter type.** The questionnaire starts with the standard questions for the matter type, and the upload hint shows examples for that practice instead of one generic list.

## 2026.09.11.11 — Declined signatures and resilient signing

Released September 11, 2026.

When a client declines to sign, the matter shows it and the follow-up stops. A signature is not lost if file storage is briefly unavailable, and moving around a matter no longer hits the rate limit.

- **Declines reach the matter.** The decline and the client's reason appear on the matter timeline, the chase task closes, and the paperwork drawer marks the form declined.
- **Signatures survive a storage outage.** If cloud storage is briefly unavailable, the client's typed signature is kept and the request finishes on retry without asking them to sign again.
- **Smoother matter navigation.** Opening and moving around a matter no longer trips the rate limit that briefly blocked dashboard, documents, messages and tasks.

## 2026.09.11.10 — Prepare paperwork from a firm template

Released September 11, 2026.

Start this case now prepares the fee agreement or an additional form from a firm template without leaving the matter, with values filled from the matter, and a finished form can be uploaded directly.

- **Prepare the fee agreement from a firm template.** Choose a firm template from the paperwork drawer, review it with values filled from this matter, and save it as the fee agreement in one step.
- **Add an additional form the same way.** The Additional forms card prepares a signing form from a firm template too; it is saved to the matter and checked for signature automatically.
- **Attach a completed form directly.** Additional forms now take a Choose a file upload, so a filled form can be attached without first saving it to the matter's documents.

## 2026.09.11.9 — A front door to your client portal

Released September 11, 2026.

Clients sign in with the email their firm has and a one-time code, the portal carries the firm's own name and contact details, and clients with more than one matter can switch between them.

- **Sign in with a code, not a password.** Enter your email and type the one-time code we send. Sessions last 24 hours, then a fresh code gets you back in, even after the invitation link expires.
- **The portal looks like your firm, not like software.** Your firm's name, logo, phone, email and website now appear on the portal and in the sign-in email, with a "Need help?" footer close at hand.
- **Always clear which matter you are in.** If you have more than one matter, choose one at sign-in and switch any time. Every upload names the matter so documents land in the right place.

## 2026.09.11.8 — Start with a 30-day trial

Released September 11, 2026.

New firms can start a 30-day LawHand trial, try the standard platform without premium AI, and the operator is alerted and can end any trial.

- **A 30-day trial, set up automatically.** Signing up now starts a 30-day trial window, so a new firm can try the platform without a payment step or a setup call.
- **Premium AI stays off during the trial.** Premium AI cannot be switched on until a firm converts, so trial usage stays on the standard models.
- **Operators are notified and in control.** The operator gets a note when a trial starts and can extend, end, or revoke any trial from the platform console.

## 2026.09.11.7 — Start paperwork from a filled form

Released September 11, 2026.

The sample form library loads again, Start this case can fill a firm template or starter form and attach it, and creating a matter is one short form.

- **The sample form library works.** Template Studio's shared library loads its ready-to-fill starter forms again, ready to preview or fill and download.
- **Fill the fee agreement in Start this case.** Choose a firm template or a shared starter form, fill it out, and attach the finished PDF as the fee agreement or an additional signing form.
- **Create a matter in one step.** The New Matter form no longer includes the optional intake packet; client paperwork is started from the matter page once the matter exists.

## 2026.09.11.6 — Live in the matter, miss nothing

Released September 11, 2026.

The matter page now tells you when a client signs, the email composer fills in the client's address and can name the outstanding records, and Client Portal is a main tab.

- **Know the moment a client signs.** When the fee agreement is signed, an on-screen notification appears on the matter and the signature list refreshes itself — no reload needed.
- **The email composer does the typing.** Emailing the client from a matter now fills in the client's email address, and one click inserts the list of records the client still needs to upload.
- **Client Portal is a main tab.** The client portal sits in the matter's primary tabs instead of hiding behind Matter settings, and the client name on a matter links straight to the client record.

## 2026.09.11.5 — Say which records you still need

Released September 11, 2026.

Paperwork emails now name the records the client must upload, follow-up tasks show a normal date, and calendar entries use the task name.

- **Ask for the records up front.** The first paperwork email and the portal-ready email now list the records the client was asked to upload, not just that something is needed.
- **Readable follow-up dates.** A follow-up task shows a normal date and time in the client's timezone instead of a raw machine timestamp.
- **Calendar names the job.** A calendar deadline created from a task now uses the task title instead of its internal type.

## 2026.09.11.4 — Texts about the case, only where the client agreed

Released September 11, 2026.

Clients who agreed to be texted about their case now get a text when a document needs signing, separately from onboarding texts.

- **A separate permission for case texts.** Agreeing to onboarding texts is recorded apart from agreeing to texts for the rest of the case, so each is asked for and held on its own.
- **A text when something needs signing.** Where that permission is held, a client is texted as well as emailed when a document is sent for signature. Existing opt-outs still apply.

## 2026.09.11.3 — Your sent email is filed with the matter

Released September 11, 2026.

Email you send is kept with the case, matter numbers appear where you read your matters, and the list you build stays put.

- **A copy of what you sent.** An email sent to a client is filed with the matter as a document, alongside the ones that arrive. Only a message that actually sent is kept.
- **Matter numbers where you look.** A matter number now shows on the board card and in the list, and searching for one finds the matter. Quote it on the phone and type it into search.
- **Your filters stay put.** Filters, search, and the board or list choice are kept in the page address, so opening a matter and coming back no longer clears them.

## 2026.09.11.2 — Close a case, and stop losing email attachments

Released September 11, 2026.

Matters can now be closed from the matter page, with a check of what is still outstanding, and documents file themselves into the right folder.

- **Close a matter properly.** A Close matter button shows what is still outstanding first. Unbilled work and money held in trust stop the close; open tasks and paperwork are shown to acknowledge.
- **Reopen if you close too soon.** Closing is reversible. A closed matter can be reopened from the same place, and both are recorded on the matter's timeline.
- **Email attachments become real documents.** A file attached to an email now arrives as its own document in the matter, next to the message, instead of only inside the stored email.
- **Documents file themselves.** Correspondence, signed agreements, intake paperwork and generated drafts land in named folders instead of piling up at the top of the matter.

## 2026.09.11.1 — Run a case from one page

Released September 11, 2026.

The matter page now opens on the paperwork a new case needs, tracks every signature in one place, and gives each document its own due date.

- **Start a case in one step.** A new matter opens with one prompt to send client paperwork: choose the fee agreement, forms, and questionnaire, then send by email or text.
- **Watch every document until it is signed.** One strip shows each document, what is outstanding, what the client returned, and any message that did not arrive.
- **Give any document a due date.** Set a deadline on any document. Each raises an assigned follow-up, due at 5pm in the client's timezone, and closes when the paperwork arrives.
- **A calmer matter page.** Overview, Documents, Activity, and Billing are the page you work from. Team, Workflow, and the portal group under Matter settings.

## 2026.09.10.3 — Browse sample legal forms in Template Studio

Released September 10, 2026.

Open Template Studio to see a shared library of fillable sample forms organized by state and type.

- **A shared library of sample forms.** Template Studio now shows ready-to-fill sample forms such as wills, powers of attorney, and business agreements, organized by state and type.
- **Fill a sample for any matter.** Preview and fill a sample form directly from the library without saving a copy.
- **Your firm's templates stay separate.** Sample forms are read-only shared content; your own templates and drafts are left untouched.

## 2026.09.10.2 — A friendly heads-up while we polish things

Released September 10, 2026.

When a new version of LawHand is on its way, everyone signed in — clients included — now sees a short, kindly worded notice before anything changes.

- **A gentle warning before an update.** During the few minutes a release takes, a slim banner explains that LawHand is being polished and saved work is safe. It never blocks you and vanishes when the update finishes.
- **Clients see it too.** Portal users get the same heads-up as firm staff, so a brief maintenance message never comes as a surprise.

## 2026.09.10.1 — Every matter now has a number you can say out loud

Released September 10, 2026.

Matters carry a short, readable number like SMIT0001 that you can share, bookmark, and quote to a client.

- **A readable number on every matter.** Each matter gets a number built from your firm's name and a running count, shown at the top of the matter with one click to copy it.
- **Open a matter by its number.** A link like /matters/SMIT0001 opens the matter, so a number written in an email or read over the phone leads straight to the right place.
- **Clients can quote their matter number.** The portal shows the same number, giving clients a reference to use when they call or write. A number is unique to your firm and never changes.

## 2026.09.09.1 — Standard client paperwork for every new matter

Released September 9, 2026.

Start each client with the same fee agreement, intake form, and questions matched to the matter.

- **Add the standard paperwork to your library.** Template Studio can add a standard fee agreement and client intake form. Both arrive as drafts for attorney review, and your own templates of the same name are left alone.
- **Ask the questions that fit the matter.** Load a questionnaire and requested uploads chosen for the matter type or practice area, then edit them before sending.
- **Collect client details once.** The intake form gathers identity, contact, conflict-check, and billing details in named fields, so later documents fill from what the client already provided.

## 2026.09.08.16 — Prepare client paperwork from the matter workspace

Released September 8, 2026.

Choose forms, organize documents, and follow up when the fee agreement is signed.

- **Work from a simpler matter view.** Customize visible matter fields with the gear, use Folder or Detailed views, preview a document before sending it, and attach reviewed templates from the library.
- **Send and track selected paperwork.** Choose forms, review them before signing, and track signatures separately. Stored fee-signing evidence opens portal access and creates the 24-hour follow-up.
- **Prepare templates and email attachments.** Draw named fields and whiteout areas in Studio. Review files before emailing them, and copy documents into another folder without changing the source.

## 2026.09.08.15 — Save reviewed Word-to-PDF documents reliably

Released September 8, 2026.

Unchanged Word template values remain consistent between preview and saving to a matter.

- **Finish the reviewed document workflow.** Save a reviewed Word-to-PDF document without a false mismatch caused by its internal file timestamps. Changed values still require a fresh preview.

## 2026.09.08.14 — Review generated PDFs directly in Studio

Released September 8, 2026.

Inspect generated documents without depending on a browser PDF plug-in.

- **Check every page before continuing.** Generated PDF and Word-to-PDF previews include page navigation, fit-to-width and zoom, with clear loading and error messages.
- **Finish missing information faster.** Keep required blanks and unreviewed suggestions visible, move through the current review queue, and open firm or matter details to correct the source without losing entered values.

## 2026.09.08.13 — Review long Word templates with confidence

Released September 8, 2026.

Find fields across document pages, work through uncertain mappings, and distinguish a draft preview from a publication test.

- **Find the detail you need.** Search labels and source text, filter fields needing review, and locate selected fields across pages without guessing ambiguous locations.
- **Keep long documents reviewable.** Long Word imports retain more complete source context. Jump directly to a page and move between document and fields on smaller screens.
- **Know what your test proves.** Draft output says Preview ready. Separate progress messages and a clear next step guide you toward testing and publication.

## 2026.09.08.12 — Prepare recurring matter work with named service identities

Released September 8, 2026.

Create a narrowly scoped firm service identity from a completed workflow and require legal approval before it can prepare recurring work for review.

- **Approve the exact recurring plan.** Rules freeze a completed workflow's bounded inputs and verified sources. A named noninteractive identity cannot sign in, receive roles, or approve its own work.
- **Keep recurring work reviewable.** Daily, weekly, and approved lifecycle-event schedules create durable proposals with the approving attorney as reviewer. Existing review controls still decide every final action.

## 2026.09.08.11 — See connected assistant activity across your firm

Released September 8, 2026.

Connect a firm-approved assistant and inspect its access, proposed work, and recorded reviews from MCP settings.

- **Follow work through review.** See active connections, recorded reads and proposals, and links to workflow and review tasks. Inspect the exact artifact revision and recorded review evidence.
- **Keep assistant access bounded.** Shared connection and firm read budgets cover direct calls and durable workflows. Assistant inference remains separate from metered LawHand premium features.

## 2026.09.08.10 — Continue bounded workflows across review and interruptions

Released September 8, 2026.

Start a matter workflow in LawHand Chat or a connected assistant, supply missing information, and continue from durable review checkpoints.

- **Continue work after review.** Inspect workflow steps from the matter or firm view. Completed work stays recorded, and each proposal uses the existing LawHand review controls.
- **Recover without duplicate documents.** Interrupted cloud writes retain the original artifact and provider evidence. Verify the existing file before continuing instead of uploading another copy.

## 2026.09.08.9 — Keep signing fields ready for e-signing

Released September 8, 2026.

Prepare template documents without entering signatures or signing dates before the signer receives them.

- **Complete only the information needed now.** Signatures and dates assigned to a signer stay out of Smart Fill and completion requirements. Ordinary dates still work as normal template values.
- **Leave signing areas ready for the signer.** Generate Word and PDF documents with blank signing fields. Clear old PDF signing-date form values and retain signer roles for final PDF placement review.

## 2026.09.08.8 — Prepare workflow drafts from your firm's history

Released September 8, 2026.

Turn repeated work and imported Clio or Tabs3 history into evidence-backed workflow drafts for your firm to review.

- **Start with observed practice.** Analyze recurring tasks, timing, assignments, document-template use, and correspondence. Inspect the evidence and approve each workflow through the existing review controls.
- **Use migration history during setup.** Tabs3 bundles prepare suggestions automatically. Map Clio or Tabs3 CSVs without joining spreadsheets. Declined patterns stay suppressed; changes appear as draft amendments.

## 2026.09.08.7 — Prepare workflows from everyday firm events

Released September 8, 2026.

Approved automation rules can prepare reviewable workflows when tasks, documents, intake, signatures, invoices, payments, and accepted matter email change.

- **Choose more workflow triggers.** Use task completion, document receipt, intake submission, completed signatures, accepted inbound email, or payment receipt to prepare an approved workflow for review.
- **Prepare for deadlines and overdue invoices.** Watch deadlines within three days and unpaid overdue invoices. Duplicate events prepare one run; changed facts block preparation. Applying a workflow still requires approval.

## 2026.09.08.6 — Work directly on documents in Template Studio

Released September 8, 2026.

See more of the document, place PDF fields where you click, and revise Word wording while keeping mapped fields.

- **Put the document first.** A focused canvas, compact tools and simpler field settings reduce navigation. Place PDF fields on the page and complete values beside a source reference.
- **Revise Word wording in context.** Edit a paragraph and save a separate revised draft. Nearby field mappings shift with the text; edits through included fields are blocked to protect their replacements.

## 2026.09.08.5 — Reuse shared firm details in templates

Released September 8, 2026.

Map template fields to the firm profile and maintain common contact details once across matters.

- **Maintain firm details once.** Choose firm name, address, phone, email, or website as a template source. Smart Fill reads the current firm profile, even before a matter is selected.
- **See shared values and missing setup.** The field library shows configured firm values and their template usage. Profile values are distinguished from AI confidence estimates, and personal document edits remain separate.

## 2026.09.08.4 — Review exact document revisions before delivery

Released September 8, 2026.

Review assistant documents in a staff-and-attorney sequence or an attorney-only sequence, with a lasting record of the exact file approved.

- **Review policy for your firm.** Choose the review sequence for new drafts. Assigned reviewers can approve, request changes, or record a reasoned attorney override in chat and the work board.
- **Send the reviewed document.** Ask your assistant to attach an approved document to a client email. LawHand checks the exact file again and requires a separate email approval before sending.

## 2026.09.08.3 — Reuse templates and finish matter fields faster

Released September 8, 2026.

Create separately named template variations and review matter-filled values with clearer completion and confidence.

- **Create reusable variations.** Copy a saved document and its fields into an independent draft without uploading the sample again.
- **Find the remaining fields.** See completion and suggestion confidence, jump to missing fields, and review changed matter values while keeping your entries. Switching matters clears the previous values.

## 2026.09.08.2 — Bound connected assistant usage

Released September 8, 2026.

Workspace connections share a call budget across refreshed tokens and receive clear guidance when a read is too large.

- **Predictable connection limits.** Connected assistants receive retry guidance when their grant reaches its call limit. Large reads return a request to narrow the query without exposing partial evidence.
- **Separate assistant and platform usage.** Workspace infrastructure limits do not debit AI balances. LawHand-run AI features retain their own usage accounting.

## 2026.09.08.1 — Restore Word previews in Template Studio

Released September 8, 2026.

Word page previews work with the installed converter, and the Studio workspace gives more room to document editing.

- **See the original Word layout.** A standard initial-page setting from LibreOffice no longer causes valid document previews to fail.
- **Keep working on fields.** Optional setup controls sit below the editor. Add field provides text-selection guidance when page rendering is unavailable or still loading.

## 2026.09.07.23 — Reliable background model pricing

Released September 7, 2026.

Background profiles can activate with native DeepSeek V4 and verified free OpenCode Zen models.

- **Save supported background profiles.** Native DeepSeek V4 models and verified free OpenCode Zen endpoints now have explicit provider prices, including Mimo V2.5 Free.
- **Meter free responses accurately.** Confirmed free-model responses settle at zero cost while request limits and unknown-cost safeguards remain enforced.

## 2026.09.07.21 — Show connected calendar times accurately

Released September 7, 2026.

Microsoft Calendar events now show at their actual local time and all-day events stay on the selected date.

- **See the right appointment time.** Connected Microsoft Calendar events include their timezone when LawHand displays them, so a UTC event is shown at the correct local time.
- **Keep all-day events on their date.** All-day Microsoft Calendar events remain on the calendar date chosen in Outlook instead of shifting when you view them in LawHand.

## 2026.09.07.20 — Preserved cloud folder connections

Released September 7, 2026.

Cloud reconnect and retry now preserve existing folder IDs while adding newly connected storage providers.

- **Keep established folders linked.** Existing OneDrive, SharePoint, and Google roots stay linked even when they have a legacy name, keeping matter folders aligned.
- **Flag incomplete root records.** Setup now reports incomplete saved root records for administrator repair instead of silently switching to a new folder.

## 2026.09.07.19 — More reliable assistant document context

Released September 7, 2026.

Follow-up questions retain eligible attachments, cloud PDF and Word files use text extraction, and source limitations stay visible.

- **Continue working with your documents.** Eligible attachments remain available for follow-up questions. Cloud PDF, Word, and retained email use text extraction; long excerpts disclose their coverage limit.
- **See retrieval limits and source counts.** Public authority outages remain visible after reopening an answer. Citation counts and uploaded-source labels remain consistent across the conversation.
- **Keep planning on an approved route.** Connected-source planning follows the selected chat route and its confidential-context policy. Blank search plans cannot expand into a mailbox-wide query.

## 2026.09.07.17 — Dedicated premium template AI

Released September 7, 2026.

Premium template field suggestions now have a dedicated Opus 5 profile and separate usage accounting.

- **Choose template AI separately.** Activate Opus 5 through a stored OpenRouter key specifically for Template Studio. Chat and background automation profiles keep their current settings.
- **Track premium template usage.** Template suggestions record their token usage and configured costs separately. The feature explains when its dedicated profile has not been activated.

## 2026.09.07.16 — Template-aware AI field suggestions

Released September 7, 2026.

AI field suggestions now use your current template setup and optional requirements while preserving your edits.

- **Explain what the template needs.** Add optional requirements before requesting AI suggestions. The proposal considers your title, category, current fields and supported data-field definitions.
- **Keep your changes.** AI suggestions add new fields without replacing your draft or reintroducing excluded fields. Results for an older draft are discarded with a clear explanation.

## 2026.09.07.15 — Reliable cloud folder retries

Released September 7, 2026.

Cloud setup can now lock and repair an unbound matter folder in PostgreSQL.

- **Repair unbound matter folders.** Retry cloud setup now handles matters that do not yet have a cloud folder, including matters without a partner attorney.
- **Keep retries moving.** Matter folder repair locks only the matter record, so a nullable relationship cannot stop the rest of the firm's setup.

## 2026.09.07.14 — Clearer document-first Template Studio

Released September 7, 2026.

See Word pages during upload, add fields from source text, and understand test results before publishing.

- **See the document immediately.** Word page previews begin when you choose a file, before saving a template. Select words directly on the page to create a named field, or click a visible field box to edit it.
- **Know what needs attention.** Field lists show replacement text and review status. Test results identify missing values and generation failures, and separate successful generation from visual approval.

## 2026.09.07.13 — Verified cloud folder recovery

Released September 7, 2026.

Cloud folder recovery now has a database-level check that covers a temporarily busy matter.

- **Keep the rest of setup moving.** When one matter folder is temporarily busy, setup continues for the other matters and reports the one that needs another retry.
- **Check real database behavior.** Release checks now verify recovery from a database failure without relying on the order matters happen to be returned.

## 2026.09.07.12 — Reliable cloud folder recovery

Released September 7, 2026.

Cloud setup can finish the rest of a firm's matters when one folder is temporarily busy.

- **Retry the matters that need it.** A temporary lock on one matter no longer stops setup for every other matter in the firm. Retry later to finish only the affected matter.
- **See partial results clearly.** The integrations page now reports how many matter folders were set up and how many still need another retry.

## 2026.09.07.11 — More reliable Template Studio AI proposals

Released September 7, 2026.

Premium AI field suggestions now reuse the completed local document analysis for the exact uploaded source.

- **Keep your reviewed scan.** Template Studio securely carries the completed local analysis into the optional Premium AI suggestion step, avoiding a duplicate document scan.
- **Keep source checks in place.** AI suggestions still require the same user, tenant, file name, and uploaded bytes. If the source changed, analyze it again before requesting suggestions.

## 2026.09.07.10 — Reliable connected matter workflows

Released September 7, 2026.

Import matter files, retrieve connected content, keep calendar times aligned, and send and reopen matter email.

- **Import and find matter files.** ZIP and folder imports preserve their files and paths. Private matter retrieval includes uploaded files in custom folders, and Cloud Search displays fetched content.
- **Keep calendar times aligned.** New scheduled events preserve the selected local time across LawHand and the connected calendar. Open event details, return to the provider, or delete the event from LawHand.
- **Send and reopen matter email.** Matter email uses the connected mailbox. Newly captured messages retain their cloud identity so their archived .eml can be reopened.
- **Review delivery outcomes.** Failed and unconfirmed attempts are labeled in correspondence. Unconfirmed delivery asks you to check Sent Items before sending again.

## 2026.09.07.9 — Turn reviewed Word spans into safe placeholders

Released September 7, 2026.

Reviewed Word source spans can become selectable placeholders while the original upload remains available as evidence.

- **Keep the original upload as evidence.** Studio records separate original and derived source digests so a changed upload invalidates older review and test evidence.
- **Preserve Word formatting.** Confirmed spans become literal placeholders across split runs, tables, headers, and footers without rebuilding the document as plain text.
- **Review prose and forms explicitly.** Studio suggests a source mode while allowing a deliberate override; ambiguous or inferred text stays available for review.

## 2026.09.07.8 — Place signature fields on generated PDFs

Released September 7, 2026.

Template Studio can bind signature, date, and initials fields to signer roles and preserve their positions through provider dispatch.

- **Bind fields to signer roles.** Signature, date, and initials fields retain their assigned signer role and are validated against the exact generated PDF revision.
- **Preserve provider placement.** Dropbox Sign requests receive positioned tabs with explicit page and coordinate conversion; unsupported internal portal placement fails closed.

## 2026.09.07.7 — Cover source details in PDF templates

Released September 7, 2026.

Place value-less white cover regions over ruled lines or stale source values while preparing a PDF template.

- **Cover what is underneath.** Add, move, resize and remove visible cover regions in Template Studio without creating automation variables.
- **Keep source-backed fields clean.** Choose whether an editable field paints over its source before the generated PDF is flattened.

## 2026.09.07.6 — See Word masters as printable pages in Studio

Released September 7, 2026.

Inspect the saved Word source with page previews while retaining text field mapping.

- **Recognize your document.** Review letterhead, tables and page layout with thumbnails, zoom and page navigation.
- **Keep mapping when preview is unavailable.** Switch to Fields for text mapping, with a retry option when document conversion is busy or unavailable.

## 2026.09.07.5 — Prepare reusable Word templates with clearer source review

Released September 7, 2026.

Review retained sample values, keep repeated amounts independent, and find shared fields and their template mappings in the Field Library.

- **Review the remaining source details.** New Word masters ask you to map suggested blanks and sample values or deliberately keep them fixed before publishing. Save your decisions and test the resulting version.
- **Keep different answers separate.** Repeated generic placeholders become separate fields. Link values explicitly when they represent the same fact, and select clear answers for recognized choice marks.
- **Map fields in document context.** Studio shows tables in source order, common numbering and emphasis, and highlights existing fields. Word inspection and unchanged fills preserve the original formatting.
- **Find reusable fields across templates.** Find shared client, matter and custom fields, see their template mappings, and open them in Studio. Authoring guidance explains Word placeholders and formatting.

## 2026.09.07.4 — Prepare provider changes with clearer account and storage controls

Released September 7, 2026.

Use verified correspondence aliases, review provider tier limits, and manage storage migration discovery from Administration.

- **Match verified correspondence aliases.** Administrators can add a secondary address for a user; the address must complete email verification before it can identify a sign-in or match internal correspondence.
- **Review storage migration evidence.** Administrators can start a provider migration, reconcile matter and document bindings, review matched, missing, and ambiguous items, and explicitly confirm a clean cutover.
- **Understand account-tier limits.** The cloud support guide explains personal-account limits, directory-sync expectations, custom-domain aliases, and provider-bound file custody.

## 2026.09.07.3 — Search your firm's whole archive from one box

Released September 7, 2026.

Firm Memory leads with a single research question instead of a wall of filters, and finds documents that were never linked to a matter.

- **Ask a question, then narrow.** One search box and a scope choice replace six filters. Matter, source, file type and dates now sit behind Refine, and anything you set stays visible as a chip you can remove.
- **Find old records nobody linked to a matter.** A research question no longer has to match every word in one passage, so a half-remembered phrase finds a document filed years ago. Quoted phrases and AND, OR and NOT still work.
- **See what was searched.** Matched words are highlighted in every result. A complete search says so, and an incomplete one names the reason and who can clear it.

## 2026.09.07.2 — Transfer client folders through the portal

Released September 7, 2026.

Send multiple files and folders with per-file results, share cloud transfer links, and refresh matter document lists.

- **Bring a folder of documents.** Clients can select or drop files and folders into their invited matter, preserve subfolders, and retry failed files. Reselecting unchanged source files avoids duplicate uploads.
- **Use a shared folder.** Clients can send a source link for legal-team review. Staff can explicitly publish a client-safe upload-folder link in the portal.
- **Refresh the document list.** Refresh saved document lists after uploads. Connected cloud-folder sync remains separate; saving or refreshing files does not mean content indexing or OCR is complete.

## 2026.09.07.1 — A workspace view for every role

Released September 7, 2026.

Give staff a focused workspace and let each person arrange their own navigation.

- **Choose functions by role.** Administrators can configure role view profiles using Receptionist, Finance, Secretary, Paralegal, Attorney, and Partner starting points, then assign those roles to staff.
- **Make navigation your own.** Use the navigation gear to hide and reorder available functions or restore role defaults. Your saved layout follows your account on desktop and phone.

## 2026.09.06.8 — Correct everyday work and keep your place

Released September 6, 2026.

Edit task details, return to the right matter section, and recover from estate-entry errors while reducing unnecessary background work.

- **Keep the selected work in view.** Return to the right matter section with bookmarks and browser Back. Document review returns to Documents, and new tasks inherit the selected matter.
- **Correct work and recover from interruptions.** Correct task details and clear dates without recreating work. Estate forms explain errors and retain failed drafts. Intake recovery and Studio Undo are more reliable.
- **Use fewer resources.** Assistant lists load where they are used, portal message polling waits for pending reads, directory sync reuses user lookups, and oversized chat attachment reads are bounded.

## 2026.09.06.7 — Keep new-client intake moving from paperwork to meeting

Released September 6, 2026.

Start a reviewed fee agreement and client questionnaire from Create Matter, deliver secure portal invitations by email or SMS, and track document and meeting follow-ups.

- **Start intake as part of matter creation.** Authorized attorneys, paralegals and secretaries can prepare the packet, choose email or SMS and assign responsible staff.
- **Wait for both completed documents.** The signed agreement and questionnaire are tracked independently. Both completed requirements close the seven-day follow-up and create a scheduling task due within 24 hours.
- **Work with Microsoft 365 or Google.** Use connected email and storage, existing staff task calendars and the secure client portal. Review uncertain delivery and record a conference call or in-person meeting.

## 2026.09.06.6 — Bring existing matters and historical email into LawHand

Released September 6, 2026.

Import USB folders or ZIP files from New Matter, review client and matter mappings, and preserve historical emails in Correspondence.

- **Create many matters from folders.** Authorized staff review folder mappings, clients and intake stages before creating matters.
- **Preserve former-firm email.** EML messages retain their original files and appear in Correspondence without requiring a matching email domain.
- **Resume incomplete imports.** Review each file result and retry unfinished uploads using the saved import ID.

## 2026.09.06.5 — Keep everyday casework within reach on your phone

Released September 6, 2026.

Matter pages now put notes, documents, tasks, workflow review and client contact together on small screens, with visible note-save recovery.

- **Reach your matter work quickly.** Choose any matter section from a phone-friendly picker, see the current stage and open tasks, or jump directly to documents, review and client contact.
- **Retry a note without duplicating it.** Interrupted note saves keep text on the open page. Confirmed saves show a receipt; retrying the same request creates one note and activity event.
- **Know where a file can open.** Phone users see the Windows network-file opener limitation and can use existing permitted document or provider links. Email forms fit shorter screens.

## 2026.09.06.4 — Keep workflow preparation visible and recoverable

Released September 6, 2026.

Matter workflow rules now queue preparation reliably with the matter save and show progress through review.

- **Recover interrupted planning.** Queued workflow preparation retries temporary failures without duplicating a reviewed run.
- **Review prepared work from the matter.** See pending, retrying, planned, blocked and failed activity, then open a prepared run before explicitly approving it.
- **Recheck changed facts and permissions.** Changed matter facts, rules, templates or actor permissions require a fresh manual review instead of silently replaying old events.

## 2026.09.06.3 — See the limits of research evidence

Released September 6, 2026.

Research checks source jurisdiction before treating a requested location as covered and keeps source capture details visible with cited authorities.

- **Know which jurisdiction a source supports.** Wrong-jurisdiction and unknown-jurisdiction hits no longer fill a requested coverage gap.
- **Inspect source dates and status.** Cited public sources show catalogue status, retrieval and sync dates, and unknown values. These details do not establish current law or treatment.

## 2026.09.06.2 — Keep approved templates available while preparing the next version

Released September 6, 2026.

Template Studio keeps the published wording available during draft edits and adds reviewed matter-document details and clearer scenario selection.

- **Draft without interrupting the team.** Test the next draft separately while colleagues generate from the last published version.
- **Review details from source documents.** Find labeled details in Word, PDF and text matter documents, correct them, then explicitly accept them into supported matter fields. Existing values require confirmation.
- **Choose the right scenario.** Name a scenario and require a matching saved matter or client detail, including Yes/No answers, before generating. Missing details stay unresolved.

## 2026.09.06.1 — Firm Memory keeps native file permissions intact

Released September 6, 2026.

Firm Memory now withholds results when native file authorization cannot be established. An opt-in on-premises OpenSearch path connects document extraction and portal search with permission and freshness checks.

- **Unavailable authorization stays private.** An offline or unhealthy file-search agent no longer falls back to cloud filenames and snippets when native permissions are required.
- **A bounded on-premises search pilot.** Configured nodes use isolated extraction and OpenSearch, checking live file access before returning results. Customer-scale and OCR acceptance require a rehearsal.

## 2026.09.05.2 — Take a Word template all the way to signature

Released September 5, 2026.

Template Studio can now turn a completed Word template into a reviewed PDF without losing the editable DOCX option, closing the handoff from document preparation to e-signature.

- **Choose editable or ready to sign.** Generate an editable Word document for another drafting pass, or choose PDF for signature when the document is final.
- **Review the exact PDF before saving.** The converted PDF opens in the Studio preview and is bound to its matter and field values before the final matter file is created.
- **Keep conversion bounded and validated.** Word conversion runs with time, size, and page limits in a private workspace, and active or malformed PDF output is rejected.

## 2026.09.05.1 — Test and publish the exact template your firm approved

Released September 5, 2026.

Template Studio now separates drafting, testing, publishing, and pausing. Every version number names exact immutable wording, and matter generation refuses anything except the published version.

- **Test before your team can generate.** A draft must render successfully with representative values before an authorized user can publish that exact version.
- **Know exactly which wording was used.** Every edit creates an immutable resulting version, and matter generation records and verifies the published version instead of trusting a changing draft.
- **Work queues that match the whole library.** Continue setup, source problems, awaiting publication, and published queues now use firm-wide results rather than whichever page happens to be open.

## 2026.09.04.2 — Templates that fill themselves and keep their history

Released September 4, 2026.

Template fields can say where their value comes from, clauses can appear only when they apply, repeating sections scale with the matter's parties, and every published version is kept so you can restore an earlier wording.

- **Tell a field where its value comes from.** Pick a data source for each field — case number, client address, plaintiff, attorney — and it fills from the matter every time, whatever you named it.
- **One template instead of near-identical copies.** Mark a clause to appear only when a field applies, with {{#if}} in the document itself. Two near-identical templates become one.
- **Repeating sections that follow the matter.** Wrap a block in {{#each parties}} and it renders once per party on the matter — a signature block each — with no editing afterwards.
- **Version history with restore.** Every saved state has its own exact version number. The Versions tab shows what changed and restores an earlier version as a new draft without retyping it.
- **Mark up Word templates by hand.** See the document itself: highlight text to make it a field, and select paragraphs to make them conditional or repeat once per party.

## 2026.09.04.1 — Plan matter workflows automatically, approve them the same way

Released September 4, 2026.

Firms can have an approved workflow template prepared the moment a matter opens or reaches a stage. The automation only prepares the work; a reviewer still approves and applies it.

- **Two bounded triggers, one action.** A rule fires when a matter is opened or enters a named stage, optionally narrowed by matter type or practice area, and plans one approved workflow template. Nothing else.
- **Automation is approved like legal work.** A rule is created as a draft and does nothing until an approver activates the exact definition they reviewed. Editing a rule returns it to draft for a fresh approval.
- **Nothing is applied without a person.** A matching rule creates the same reviewable preview the Workflow tab creates by hand. Tasks and matter stages change only when someone applies the planned run.
- **A rule that does nothing says why.** Every match is recorded as immutable evidence naming the rule, the matter, and the planned run, or the reason it was blocked. The matter's Workflow tab shows it too.

## 2026.09.03.1 — Firm-wide Firm Memory research that opens the document

Released September 3, 2026.

Firm Memory research now searches document text across every matter you are authorized on, says what it could not cover, and every on-premise result opens to its matter file.

- **Ask without picking a matter first.** A query with no matter filter searches the matters on each file share you are already authorized on, decided by the same matter policy a chosen matter goes through.
- **Search inside documents, not just file names.** Results come from the firm's own search node with real page numbers and passages; if that node cannot be reached, the response says so.
- **Open every result you find.** An on-premise result links to its matter file, which rechecks the matter binding, the live index entry, and the bound folder before showing it.
- **Know what a search did not cover.** Every incomplete response states in one sentence why, so “no matches” is only ever said about a search that actually covered its sources.

## 2026.09.02.1 — Organize matter documents with folders and tags

Released September 2, 2026.

Case documents get a file-explorer view: a per-matter folder tree, firm-wide tags, search and sort, and uploads that write into the matching folder of the firm's bound cloud share.

- **Group case files into folders.** Each matter has its own folder tree with drag-and-drop filing, breadcrumbs, and per-folder counts. Deleting a folder never deletes the documents inside it.
- **Label documents across matters.** Firm-wide tags apply to any case document and filter the list conjunctively, so “Signed” plus “Filed” narrows to documents carrying both.
- **Find a document without scrolling.** Filename and description search, folder scoping with or without subfolders, and name, date, or size sorting all run on the server rather than in the browser.
- **The cloud share mirrors the same tree.** A document uploaded into a folder is written to the matching folder under the matter in the firm's cloud share, and portal uploads file themselves into Client Uploads.

## 2026.09.01.1 — Consent-aware SMS with review and opt-out controls

Released September 1, 2026.

Firms can configure provider-backed SMS that stays reviewable, consent-bound, tenant-scoped, and honest about provider acceptance and delivery.

- **Send only with current consent.** Each outbound message rechecks verified phone ownership, consent provenance, approved message category, quiet hours, matter access, and provider configuration.
- **Honor replies and opt-outs safely.** Signed inbound messages require an active owned sender, handle STOP, START, and HELP, deduplicate retries, and route ambiguous matches to staff review.
- **Keep provider truth distinct.** Exact-generation callbacks and reconciliation update authorized timeline evidence without regressing known truth; provider acceptance remains distinct from confirmed delivery.
- **Review assistant proposals first.** Workspace SMS proposals require current matter access and an explicit retry key, stay inside LawHand instead of email or third-party calendars, and cannot dispatch autonomously.

## 2026.08.31.7 — Secure opening for on-premise Firm Memory files

Released August 31, 2026.

When a firm explicitly enables the Windows helper, authorized Firm Memory results can open through the signed-in user's Windows session without placing a network path in a browser link.

- **Open with one-time authorization.** Each action uses a short-lived, single-use request bound to the tenant, user, source file, assigned agent, revision, and requested action.
- **Keep network paths local.** The browser and launch link carry no UNC, file, or SMB path; the local agent resolves the opaque source identity only after redemption.
- **Honor current Windows access.** Windows rechecks reachability and the signed-in user's SMB/NTFS access; moved, expired, offline, and denied requests fail closed with a copy-path fallback.

## 2026.08.31.6 — Durable Template Studio preview processing

Released August 31, 2026.

Template Studio now has a tenant-isolated durable job and artifact foundation for reliable preview and test-render processing.

- **Resume interrupted work.** Preview and test-render requests use durable queued jobs with bounded retries, cancellation, lease recovery, and tenant-safe status resources.
- **Keep evidence exact.** Completed output is hash-verified and can become current evidence only when the immutable snapshot, draft revision, and identity still match.
- **Protect private document data.** Job metadata excludes document values, bytes, storage paths, provider identifiers, signed URLs, and raw exception text.
- **Keep activation review-gated.** The production worker remains disabled until encrypted CAS backup and restore rehearsal are added to the release gate.

## 2026.08.31.5 — Revalidate authority evidence before alerts

Released August 31, 2026.

Public-authority alerts and coverage claims now remain bound to the exact reviewed evidence lineage and source state that supported them.

- **Stop revoked evidence from sending.** Queued authority alerts recheck the stored history or citation fact and both public source lineages before recording a successful delivery.
- **Keep coverage claims audit-bound.** Changes to coverage scope, source identity, publisher, URLs, cadence, caveats, or other customer-visible source facts suppress claims until fresh audits pass.
- **Reject mismatched citation identities.** Citation facts must link each opinion to the same reviewed source as its authority, preventing unrelated public opinions from laundering revoked or private evidence.

## 2026.08.31.4 — Fail-closed public-authority source boundaries

Released August 31, 2026.

Public legal research now serves and describes only reviewed official, open, or licensed sources that are explicitly admitted to the promoted authority release.

- **Require explicit public admission.** A source key or caller-supplied metadata cannot make legal documents or case law public; the reviewed catalog, admission, and promoted manifest must agree.
- **Suppress revoked or mismatched sources.** Source-rights, review, storage, schema, implementation, namespace, or manifest changes immediately suppress public results, status, coverage, and claims.
- **Keep private firm data out.** Tenant, firm, private, custom, and unknown records cannot enter the public-authority corpus, telemetry, counts, audits, or customer coverage projection.

## 2026.08.31.3 — Reliable file-share agent setup on managed Windows networks

Released August 31, 2026.

The LawHand file-share agent now uses Windows-managed certificate trust when connecting to LawHand, improving enrollment reliability on firms' inspected HTTPS networks.

- **Enroll without weakening TLS.** The agent recognizes approved enterprise certificate authorities deployed by the firm's Windows administrators while continuing to validate the LawHand hostname and certificate.
- **Keep the same secure connection.** No inbound network access or certificate-validation bypass is required; the agent continues to use outbound HTTPS for its assigned file-share work.

## 2026.08.31.2 — Configurable matter workflows with reviewable runs

Released August 31, 2026.

Firms can define bounded matter data and apply approved stage and checklist templates through previewed, auditable workflow runs.

- **Model firm-specific matter data.** Administrators can define typed matter fields with stable keys, required and sensitive handling, bounded options, and safe retirement controls in the matter workflow surface.
- **Preview before applying a workflow.** Approved matter templates show the exact initial stage, required data, assignee gaps, checklist tasks, and snapshot hash before any matter or task changes occur.
- **Apply once with durable evidence.** Approved runs create deterministic relative tasks in one transaction, reject stale previews, deduplicate retries, and retain immutable run and step evidence.
- **Compensate without erasing history.** Rollback cancels only unchanged workflow-created tasks, restores the prior matter stage, reports blockers for manual review, and keeps authoring separate from legal approval.

## 2026.08.31.1 — Template Studio workspaces and resumable setup

Released August 31, 2026.

Document templates now have a first-class Studio home, persistent workspaces, and clearer paths from source preparation to generation.

- **Resume template setup.** Studio home groups loaded templates into continue setup, needs attention, ready to generate, and recent views using the existing library status.
- **Open a persistent workspace.** Every template links to a stable workspace for reviewing its source, fields, readiness, preview, and generation actions.
- **Keep source preparation intact.** New and imported templates continue through the reviewed Word, PDF, and image preparation flow before opening their workspace.
- **See current limits clearly.** Reserved test, version, activity, draft, proposal, and snapshot routes say when server-backed records are not yet available instead of showing fake controls.

## 2026.08.30.9 — Shared research workspaces with reviewable evidence

Released August 30, 2026.

Matter teams can now preserve a shared research trail without turning machine notes into source evidence.

- **Keep the team trail together.** Create matter-scoped workspaces for saved issues, searches, authorities, highlights, annotations, exclusions, outlines, and memo notes with explicit member roles.
- **Carry evidence labels forward.** Every saved item and frozen export keeps its cited, verify, or model label alongside exact source links, source version, pinpoints, and stored treatment/currentness state.
- **Freeze a review package.** Immutable, hash-identified snapshots create an exportable record of the workspace as reviewed. Bluebook formatting and citation correctness still require attorney verification.
- **Revoke safely.** Workspace access is tenant-and-matter scoped, role checked, auditable, and revocable; archived workspaces and records are retained rather than silently erased.

## 2026.08.30.8 — Review-first citator evidence controls

Released August 30, 2026.

Research now separates linked source facts from provisional treatment interpretation and makes citator limits visible before attorney review.

- **Inspect the evidence state.** Citator results show the promoted source, version, as-of date, history or citation evidence, and currentness limitations separately from any machine interpretation.
- **Keep treatment reviewable.** Machine labels may abstain and remain provisional until an attorney accepts, rejects, requests more evidence, or records an override in the append-only review trail.
- **Watch changes safely.** Saved authority watches are consented, tenant-and-matter scoped, deduplicated, revocable, and ready for quiet-hour and failure-aware delivery. This release sends no alerts.
- **No unsupported status claim.** LawHand does not call an authority good law from a missing negative record. Complete citator coverage still requires a permitted licensed or attorney-reviewed benchmark.

## 2026.08.30.7 — Private mediation review and party-specific sharing

Released August 30, 2026.

Licensed mediation work now appears inside the client's existing matter portal while documents and proposals stay private until attorney review and deliberate release.

- **Keep each submission private.** A party's document or proposal is visible to that party and the firm until the legal team explicitly releases it to selected recipients.
- **Review before delivery.** Attorneys can approve, return, or reject proposals before choosing who receives an approved version; released content is preserved as immutable evidence.
- **Negotiate with traceable counters.** Counteroffers can reference only an active proposal released to that party, and the prior offer is superseded only when the reviewed counter is actually released.
- **Use mediation in My Matters.** Eligible clients see a read-only Mediation tab in their native matter portal, with scoped assets, proposals, and integrity-checked document downloads when the add-on is active.

## 2026.08.30.6 — Clearer legal research gaps

Released August 30, 2026.

Research now separates linked source facts from provisional treatment interpretation and makes citator limits visible before attorney review.

- **Keep verified findings.** When a response mixes cited and unsupported material, cited findings remain visible while unsupported claims are omitted.
- **Explain missing authority.** Research gaps now say whether the authority service or fallback was unavailable, or whether no usable match was found.
- **Open the cited source.** CourtListener and official public-source URLs remain attached to the cited source so reviewers can open the retrieved authority.
- **Avoid uncited legal summaries.** Jurisdiction-specific questions without authority no longer receive a general-knowledge answer presented as completed research.

## 2026.08.30.5 — Versioned public-authority coverage

Released August 30, 2026.

Public legal research now exposes reviewed source scope, corpus release evidence, currentness limits, and honest retrieval fallbacks.

- **See reviewed source scope.** Rights decisions, authority tiers, content types, jurisdictions, temporal scope, cadence, caveats, and provenance are carried with source health.
- **Track corpus releases honestly.** Promoted versions, rollback metadata, harvest checkpoints, quarantine states, and sampled audits prevent unsupported complete/current claims.
- **Preserve retrieval boundaries.** Exact embedding compatibility is required; outages or mismatches fall back to keyword/source search while private firm documents remain outside public telemetry.

## 2026.08.30.4 — Controlled client and matter import promotion

Released August 30, 2026.

Administrators can approve an unchanged import report before promoting the conservative client and matter subset, with durable links and rollback review markers.

- **Approve the exact reconciliation.** Promotion requires an explicit confirmation and report hash, so changed or stale staging cannot be promoted silently.
- **Reconcile existing records.** Client identifiers and email addresses are matched within the tenant before a new client is created; every promoted row receives an external provenance link.
- **Keep rollback reviewable.** Operators can mark a promoted run for non-destructive rollback review while audit receipts preserve what happened.

## 2026.08.30.3 — Search a firm's local case-file memory

Released August 30, 2026.

A bounded local file-search control surface connects an approved file-share agent to the portal, Chat, and Workspace MCP while keeping the firm's query and documents on the firm's network.

- **Search inside supported local files.** Matter-scoped searches can return ranked snippets and page hints from the agent's local SQLite control index without embeddings.
- **Use the same search from three surfaces.** The portal, Chat assistant, and user-authorized Workspace MCP now share one bounded search contract with visible partial and index status.
- **Open results safely.** Results use an authenticated portal link that rechecks access and offers Copy UNC instead of exposing a raw browser file link.
- **Measure the pilot honestly.** Correlation IDs, counts, latency, and indexed or pending status support local validation; this remains a small control-index PoC, not a 4 TB production search promise.

## 2026.08.30.2 — Deeper federal and Ohio research coverage

Released August 30, 2026.

Research can ingest reviewed federal rules, constitutional analysis, and Tax Court reports while keeping jurisdiction and known extraction limits explicit.

- **Search more reviewed federal authority.** Federal rules, Constitution Annotated, and bounded Tax Court Reports now have scheduled, failure-isolated ingestion paths.
- **Keep unsafe text out.** A known unreadable appellate-rules extraction stays auditable but cannot enter searchable text until its parser is replaced.
- **Scope Ohio and federal research correctly.** Explicit Ohio and federal questions now apply matching filters to case law and other public authorities.

## 2026.08.30.1 — Review-first Brief Check

Released August 30, 2026.

Firms can review brief citations, quotations, pin cites, opposing-brief differences, and bounded authority candidates with linked evidence before exporting work product.

- **Inspect evidence before relying on a brief.** DOCX/PDF checks preserve source identity, location, confidence, ambiguity, retrieval evidence, and explicit unknown or unavailable states.
- **Keep attorney decisions auditable.** Reviewers can accept, reject, or follow up on findings; decisions are tenant-scoped and recorded in an immutable audit trail.
- **Export linked review work product.** Download a review report and table-of-authorities draft while bounded recall and currentness limitations remain visible.

## 2026.08.29.1 — Operating trust workflows and evidence

Released August 29, 2026.

Customers and operators can use one versioned contract for support, incidents, lifecycle receipts, offboarding proof, providers, and security review without unsupported SLA or certification claims.

- **Use defined support and status workflows.** Published business hours, S1-S4 severity objectives, audited escalation, and sanitized append-only incident updates make the operating process reviewable without creating an SLA.
- **Carry acceptance evidence through the lifecycle.** Onboarding, BK28 migration, and export receipts reconcile scope and counts; offboarding blocks on holds and needs two operators before proof.
- **Export an honest security packet.** The content-addressed packet names providers and boundaries while marking penetration testing and certifications as not attained.

## 2026.08.28.6 — Attributed intake and safer lead follow-through

Released August 28, 2026.

Firms can capture attributed public inquiries, triage conflicts, book consultations, and follow up with explicit consent while keeping the conversion trail auditable.

- **Capture inquiries safely.** Conditional forms accept only validated answers, resist simple spam, preserve source attribution, and deduplicate retries.
- **Review before conversion.** Public leads require an explicit conflict decision before they can become matters; appointment booking and reminder state remain durable.
- **Respect communication choices.** Authored email follow-up checks current consent and reports provider failure truthfully; SMS remains unavailable until its compliance controls are ready.

## 2026.08.28.5 — Zoom Phone setup now shows exactly what needs attention

Released August 28, 2026.

Tenant administrators can follow Zoom Phone setup stage by stage and recover from authorization failures without leaving the integration panel.

- **See each setup stage.** The Zoom panel separately tracks saved app credentials, account authorization, Phone API permissions, and verified real-time call delivery.
- **Recover from the actual authorization problem.** Zoom authorization returns to the integration panel with specific guidance for rejected credentials, expired requests, missing permissions, and account mismatches.
- **Know when reconnecting is required.** Replacing an OAuth client pair now clearly reports that the previous grant was disconnected and directs the administrator to authorize the current app.

## 2026.08.28.4 — Client portal payments and durable sign-in

Released August 28, 2026.

Clients can activate a durable portal account, pay an invoice through hosted Stripe Checkout, and complete certified Dropbox Sign requests when the firm configures the provider.

- **Pay invoices securely.** The portal opens a hosted Stripe Checkout session for the current invoice balance; payment status updates only after verified provider reconciliation.
- **Return with durable sign-in.** Clients can activate a password-backed account and sign into an explicitly selected matter while invitation revocation remains authoritative.
- **Use certified e-signature providers.** Dropbox Sign dispatch and signed or declined webhook events are authenticated, tenant-bound, and idempotently reconciled.

## 2026.08.28.3 — Zoom Phone setup now shows the exact authorization path

Released August 28, 2026.

Tenant administrators can configure Zoom Phone with the exact required permissions and start authorization from the correct LawHand control.

- **Choose only the required Phone access.** The Zoom setup panel names both account call-history permissions, shows their exact identifiers, and links directly to Zoom's Call Logs reference.
- **Start the secure tenant connection correctly.** LawHand now warns administrators to use Connect Zoom Phone instead of Marketplace Add or generated authorization links, preserving the tenant-bound authorization check.

## 2026.08.28.2 — Demo workspaces are clearly identified for operators

Released August 28, 2026.

Platform operators can now distinguish disposable demo workspaces from regular platform tenants in the tenant inventory.

- **See the tenant type.** The platform tenant list labels each workspace as Demo or Platform without using the billing tier as the visible grouping.
- **See demo expiry at a glance.** Demo rows show their expiration and clearly flag workspaces that have already expired.
- **Filter the inventory.** Operators can focus the tenant list on demo workspaces or regular platform tenants while the dedicated demo controls remain protected.
- **Use Zen free background capacity.** Platform operators can assign OpenCode Zen free models to the Background Automations route while Standard and Premium keep their confidential-data safeguards.

## 2026.08.28.1 — The platform tour now follows a matter end to end

Released August 28, 2026.

The LawHand platform tour now shows how legal work moves from intake and conflict review through preparation, client action, signature, billing, and follow-through.

- **Follow the matter.** Step through a visual workflow from the first call to an opened matter, attorney review, client delivery, and accounting.
- **See every role's handoff.** Compare what attorneys, paralegals, intake staff, billing staff, and clients need from the same matter record.
- **Evaluate the complete platform.** Review expanded capability, practice-area, integration, and control sections with clear rollout labels and concrete workflow details.

## 2026.08.27.12 — Product claims now show rollout and research boundaries

Released August 27, 2026.

LawHand product and pricing pages now distinguish shipped behavior, controlled pilots, planned work, and provider-dependent connections.

- **See the rollout state.** Capability cards identify implemented behavior, controlled pilots, and connections that require a separate provider account or approval.
- **Evaluate Research MCP as a pilot.** Research MCP pricing and product pages now state the controlled-pilot gate and the configured public-authority coverage boundary.
- **Keep research claims precise.** LawHand explains that source links support attorney review without claiming Westlaw replacement, comprehensive coverage, or a good-law determination.

## 2026.08.27.11 — Agreement evidence and safer retention controls

Released August 27, 2026.

Firms can review current agreement acceptance evidence and preview safe expiry cleanup with legal-hold protection.

- **Know what was accepted.** Tenant admins can review counsel-owned agreement versions and record signer, authority, and immutable document evidence.
- **Retention with guardrails.** Review tenant data-store inventory, configure retention, preview cleanup, and protect held or matter data from deletion.

## 2026.08.27.10 — Research API keys put staff access and spend under firm control

Released August 27, 2026.

Firm administrators can provision LawHand Research keys for staff, bound their lifetime and budget, and see usage and charges from the MCP portal.

- **Issue keys with clear custody.** Name each key, record its purpose, assign it to a LawHand staff profile, and choose exactly which Research tools it may use.
- **Bound time and spend.** Set an expiration, monthly dollar budget, call cap, and burst limit; the gateway stops successful calls before they exceed either hard monthly boundary.
- **See the complete key ledger.** The portal shows active, expired, and revoked keys with creator, custodian, last use, successful and failed calls, current-month charges, and remaining budget.
- **Connect standard API clients.** LawHand Research keys work as standard Bearer credentials while the existing custom header remains supported for compatible clients.

## 2026.08.27.9 — Document automation is faster, safer, and easier to manage

Released August 27, 2026.

Firms can navigate larger template libraries, see readiness at a glance, and keep reviewed document generation moving with stronger recovery controls.

- **Find the right template faster.** Search titles and descriptions, filter by status or category, and move through a paged library without loading every template at once.
- **See what needs attention.** Library health cards distinguish ready templates, drafts, and binary templates whose original source must be restored before use.
- **Keep concurrent work moving.** Independent scans and tenant background work can progress within bounded capacity instead of waiting behind one unrelated long-running job.
- **Save the reviewed PDFΓÇönot a surprise.** Final PDF saves recheck the exact reviewed output and clean up or quarantine staged files when storage and database results cannot be proven consistent.

## 2026.08.27.8 — Connected assistants can prepare complete matter work for review

Released August 27, 2026.

Claude, Codex, ChatGPT, and other approved Workspace MCP clients can now gather client-to-task context, read matter documents and templates, and place prepared documents into LawHand's staged review workflow.

- **Gather the working context.** Approved assistants can search clients, intakes, matters, and tasks, then load parties, team, events, notes, communications, and history within the user's permissions.
- **Reason over documents and templates.** The assistant can read bounded text from uploaded matter documents and approved templates, including the template's fillable-field contract.
- **Send prepared documents through human review.** Fresh or template-rendered Word documents go to tenant cloud with a LawHand Review task for staff and attorney approval. Assistants cannot approve, file, send, or deliver.
- **Reconnect older one-tool connections.** Connections that still show only Find matter keep their original consent. Remove and reconnect once to review expanded scopes; LawHand never enlarges grants silently.

## 2026.08.27.7 — Research connections now complete authorization

Released August 27, 2026.

Claude, ChatGPT, and other hosted clients can now complete the LawHand Research authorization screen without supplying a separate API key.

- **Approve the connection in LawHand.** After adding the Research MCP URL, sign in and approve research-only access through the normal LawHand authorization page.
- **No pasted key for hosted clients.** Hosted clients register securely and use OAuth; Research API keys remain an option only for clients that support custom headers.

## 2026.08.27.6 — Plaintiff and defendant fields are clear and reusable

Released August 27, 2026.

Matter parties now distinguish caption roles from the client relationship, and document templates can Smart Fill reviewed singular or multi-party plaintiff and defendant names.

- **Identify the actual caption role.** The Parties tab now defines plaintiff, defendant, petitioner, and respondent alongside client, counsel, witness, and expert roles.
- **Choose the primary named party.** For matters with multiple plaintiffs or defendants, staff can mark the primary contact used by a singular template field.
- **Reuse parties safely in documents.** Templates can use explicit singular and plural plaintiff or defendant fields, with every Smart Fill value still reviewed before preview or save.

## 2026.08.27.5 — Integration administration is organized in one clear workspace

Released August 27, 2026.

Administrators can now review every connected service from one Integrations workspace with clearer purpose, permissions, setup requirements, and operating guides.

- **Find every connection in Integrations.** Cloud accounts, search, file shares, Teams, Zoom, QuickBooks, and MCP now live under one organized administrative workspace.
- **Understand access before setup.** Each integration explains what it does, the data and permissions it uses, and the prerequisites an administrator should confirm.
- **Move from overview to the right controls.** Focused sections keep configuration close at hand, while expandable notes link directly to the relevant administrative guide.

## 2026.08.27.4 — QuickBooks invoice sync is smoother and respects tax choices

Released August 27, 2026.

QuickBooks connections now return directly to LawHand, account setup is more reliable, and each invoice's sales-tax choice carries into QuickBooks.

- **Return directly to QuickBooks settings.** After approving the connection in Intuit, administrators land back in LawHand with a clear connected confirmation.
- **Configure accounts without losing other mappings.** Accounts-receivable settings save correctly, and a temporary catalogue error no longer hides every available QuickBooks option.
- **Keep non-taxable legal services non-taxable.** An invoice with a zero sales-tax rate sends non-taxable lines to QuickBooks; a positive rate sends taxable lines.

## 2026.08.27.3 — Signature requests are easier to deliver and follow through

Released August 27, 2026.

Signature requests now provide clearer delivery tracking, safer resend controls, and automatic follow-through for sequential signing.

- **Send the actionable request.** Internal signature requests email the signer who can act next and retain delivery status for staff.
- **Track and resend safely.** Staff can see delivery and first-view status and manually resend when follow-up is needed.
- **Keep sequential signing moving.** Completing one signer notifies the next, while configured reminders run before expiration.

## 2026.08.27.2 — Conflict reviews and portal invoice PDFs are traceable

Released August 27, 2026.

Staff can save and close a conflict review, and clients can download a firm-branded invoice PDF from their matter portal.

- **Conflict searches keep their evidence.** The new Conflict Search workspace saves the terms and results the reviewer saw, records notes and a decision, and locks the record after closing.
- **Restricted matters stay restricted.** A reviewer is warned when a potential match exists on a matter they cannot access without exposing that matter's identity.
- **Clients can download branded invoices.** A client-visible invoice is rendered with firm branding and streamed as a PDF; LawHand records download metadata and a hash without retaining another PDF copy.

## 2026.08.27.1 — Prepare document templates with a guided review workspace

Released August 27, 2026.

Turn PDFs and supported images into reusable document templates while reviewing every detected field before creation.

- **Review the original form in place.** Prepare Form keeps the uploaded page design visible while you inspect, add, move, rename, and configure fields.
- **Recover fields from scans and images.** Bounded local OCR and optional configured assistance can suggest fields without making automatic detection a requirement.
- **Finish safely when detection is imperfect.** Validation, confidence cues, and manual placement let you correct unfamiliar forms before creating the reusable template.

## 2026.08.26.4 — Customer cloud storage is explicit and fail-closed

Released August 26, 2026.

Microsoft 365 tenants now default matter files to OneDrive, and portal uploads keep their original copy in a dedicated customer-cloud folder.

- **Microsoft 365 defaults to OneDrive.** When Cloud Document Storage is Auto, an active Microsoft 365 connection is the authoritative destination unless an administrator selects SharePoint or Google Drive.
- **Portal originals have a stable home.** Client uploads go to the matter's client_uploads folder. A reviewed or revised output is saved as a new matter document instead of silently moving the original.
- **Cloud outages fail honestly.** A cloud-bound upload reports a retryable storage error when the customer provider is unavailable; it does not report success after saving a durable local copy.

## 2026.08.26.3 — Tagged matter email can create traceable tasks

Released August 26, 2026.

A reviewed matter email whose subject begins with [TASK] or [DEADLINE] can now be filed and turned into a linked task in one step.

- **Use an explicit subject tag.** Start a new subject with [TASK] or [DEADLINE]. The tag must be the first token, so replies and forwarded subjects do not trigger.
- **Review the task before filing.** The Correspondence queue previews the task title and any safely parsed due date before the reviewer chooses File + create task.
- **Keep the source email attached.** The filed email, correspondence record, task, and task history remain linked so the source and resulting work can be audited from the matter.

## 2026.08.26.2 — Demo workspaces reopen with approved matter-aware AI

Released August 26, 2026.

Return to an active demo without a password, and demonstrate matter-aware Standard AI when Platform has approved the route.

- **Reopen an active demo.** Choose Resume demo and enter the same email plus the current demo access code; the original expiry and AI quota stay unchanged.
- **Show matters with approved Standard AI.** Standard can use matter and attachment context when its assigned Platform routing profile explicitly allows confidential context.
- **Control the demo route from Platform.** Platform can choose the approved Standard profile assigned to new demos while Premium remains unavailable for disposable tenants.

## 2026.08.26.1 — Guides now cover matter email and connected assistants

Released August 26, 2026.

The in-product guides explain how matter email reaches a file and how to review or revoke an external assistant connected to your workspace.

- **Matter email is documented end to end.** The user guide covers the Correspondence tab: capture rules and Scan now, forwarding addresses for a matter, and the queue where you file or reject a message.
- **Connected assistants are yours to review.** The guide explains the Workspace MCP list in your profile ΓÇö what each connection holds, when it was last used, and how revoking one takes effect immediately.
- **Administrators can see who may connect.** The administrative guide covers the per-user Workspace MCP control, the default for new accounts, and how consent-based access differs from a scoped product key.

## 2026.08.25.1 — Workspace MCP connections respect your firmΓÇÖs access settings

Released August 25, 2026.

Claude, ChatGPT, Codex, and other compatible assistants can connect when your firm administrator enables Workspace MCP for your account and Privacy Mode is off.

- **Firm access settings take effect directly.** Admin ΓåÆ Users now separates firm permission, effective availability, and active OAuth connections, with a drawer to review or revoke each client.
- **Your privacy choice remains independent.** Privacy Mode continues to pause external assistants, and turning it off allows a new explicit OAuth connection when firm access is enabled.
- **Security boundaries remain enforced.** Active account, license, consent scope, role capability, token revocation, and tenant isolation checks still apply to every request.

## 2026.08.24.5 — Research answers show and link the sources they use

Released August 24, 2026.

Standard research now gives public authority to the assistant, mixed citations remain clickable, and the review-tag guide stays visible while an answer is prepared.

- **Standard answers can use retrieved public authority.** Standard answers can now use retrieved public cases, statutes, and rules with review tags. Matter and firm information remain excluded from Standard.
- **Every retained source marker stays clickable.** Answers that mix structured citation annotations with ordinary source markers now link both kinds to the authority or the in-answer source ledger.
- **Review tags and research progress stay in view.** The tag legend remains visible on phones and desktops, and retrieved-source previews and elapsed research phases remain visible while Premium prepares a validated answer.

## 2026.08.24.4 — Matter forwarding addresses can be created

Released August 24, 2026.

Creating a secure forwarding address from a matter now completes normally instead of failing when the matter has no partner attorney.

- **Create the address from Correspondence.** The Create address action now locks only the matter being updated, so the unique forwarding address appears immediately and is ready to use.

## 2026.08.24.3 — Task details stay readable on phones

Released August 24, 2026.

Mobile task rows now give the task name, due date, status, and actions their own space instead of squeezing them into one overlapping line.

- **Task names keep their space.** The task name and notes use the full mobile row width, so longer names remain readable beside the completion checkbox.
- **Dates and actions wrap cleanly.** Due dates, priority and unread badges, and task actions move onto a separate wrapping row instead of colliding or spilling off the screen.

## 2026.08.24.2 — Forward email straight to the right matter

Released August 24, 2026.

Give each matter its own secure forwarding address, see exactly which contact addresses are tracked, and review incoming mail before it becomes official correspondence.

- **Use one unique forwarding address per matter.** Create an opaque address from the Correspondence tab and forward or BCC a message there without putting a client name or matter number in the address.
- **Review mail before filing it.** Forwarded messages wait in a review queue until a firm user files or rejects them, so an email cannot silently become part of the matter record.
- **See what automatic matching tracks.** Correspondence rules now list the client and matter-party email addresses they use instead of leaving the matching behavior implicit.
- **Rotate or disable an address immediately.** Replace a forwarding address when it has been shared too broadly, or turn it off when the matter no longer needs inbound mail.

## 2026.08.24.1 — A client portal that shows clients what needs their attention

Released August 24, 2026.

The client portal now opens on what is waiting for the client, tracks what they have read, lets them sign out, and shows the firm whether an invitation is actually being used.

- **Clients see what needs them, first.** The portal opens on unread messages, documents awaiting signature, shared documents, and the balance due, with the next key date called out and how soon it falls.
- **New messages are marked as new.** Messages from the legal team show as unread and are counted on the tab until the client opens the thread, which refreshes on its own while they are reading it.
- **The legal team hears about a client message.** When a client writes in through the portal, the people assigned to the matter are emailed a short preview and a link, rather than waiting for someone to notice.
- **Clients can sign out.** A sign-out button ends the portal session immediately, so a borrowed or shared device does not keep access to the matter.
- **Invoices show what is actually owed.** Each invoice shows the amount paid, the balance remaining, and how far past due it is, with a running total for the matter.
- **See whether a portal invitation was ever opened.** The matter's Client Portal tab shows each invitation as awaiting first sign-in, active, expired, or revoked, and when it was last used.

## 2026.08.24 — Network file shares, connected with their own credentials

Released August 24, 2026.

Point LawHand at a file share that stays on your network: install the agent from a packaged installer, store the share's credentials securely, and test the connection before anyone searches it.

- **Install the file share agent instead of building it.** Windows installs from an MSI that registers a background service and can pair during setup; Linux ships a binary with a service installer.
- **Store share credentials securely, per firm.** Save the username and password, Kerberos, or guest identity each share needs. Secrets are encrypted, never shown again, and only reach your own agent.
- **Different shares can use different accounts.** One agent can serve shares that need separate identities, and a credential can be restricted so it only ever reaches one agent.
- **Test a share before trusting it.** Test connection asks the agent to mount the share and reports which identity it used, or the exact reason it failed, instead of leaving an empty index.
- **See when a share last indexed and why it stopped.** Each share now shows its last scan time, file count, and failure reason, and can be rescanned on demand rather than waiting for its schedule.

## 2026.08.23.1 — Teams calls land in your intake feed

Released August 23, 2026.

Inbound Microsoft Teams Phone calls now appear in the intake dashboard beside Zoom Phone calls, and the Teams admin panel gains notification routing and a guided voice setup.

- **Teams Phone calls reach intake automatically.** Inbound calls to your firm's Teams numbers appear in the intake dashboard beside Zoom Phone calls, with the same follow-up tasks and export.
- **Choose where each Teams notification lands.** The Teams admin panel now routes each kind of notification to a team and channel you pick, with a matter's own linked channel still taking precedence.
- **Linked channels name the matter.** The list of linked channels now shows the matter name instead of an internal identifier, so you can tell at a glance what each channel is bound to.
- **Teams errors say what to do.** When Microsoft refuses a request, the panel explains what happened and how to fix it instead of showing an empty list of teams.

## 2026.08.23 — Billing problems you can actually see

Released August 23, 2026.

Your firm is now told when a subscription payment fails, slow pages say so instead of spinning, and Privacy Mode explains what it turns off.

- **Know about a payment problem before it costs you access.** A banner now appears for everyone in the firm when a subscription payment fails or is suspended, with a direct route to update the payment method.
- **No more being offered a plan you already pay for.** If the plan on file disagrees with the subscription your firm actually holds, the billing page says so and stops prompting you to buy again.
- **Slow is now distinguishable from stuck.** Requests that take too long end with a clear message and a retry instead of an open-ended spinner, and long lists show their shape while loading.
- **Privacy Mode says what it affects.** The Privacy Mode switch now states that it also blocks connected assistants, and the connected assistants list shows when access is blocked and why.

## 2026.08.21 — A dedicated home for client relationships

Released August 21, 2026.

Manage client details, communication preferences, matters, billing choices, and accounting connections from one secure workspace.

- **Keep the complete client profile together.** Store contact details, addresses, emergency contacts, preferences, consent, internal notes, and relationship status in Clients & CRM.
- **See client work in context.** Open a client to review linked matters, activity, tasks, and billing preferences without searching across separate screens.
- **Move client data safely.** Tenant admins can import or export bounded CSV files and synchronize customer records with connected QuickBooks Online accounts.

## 2026.08.20 — A clearer view of what changed

Released August 20, 2026.

See the version currently running and catch up on the latest LawHand improvements without leaving your workspace.

- **Find updates in LawHand.** Open Profile or Admin Settings to see the running version and the latest release notes.
- **Revise documents without losing the original.** Each matter-document revision keeps the source document and its review history.
- **See where work stands.** Move tasks through To Do, In Progress, Waiting, Review, and Done with clearer ownership and history.
- **Keep AI work available during provider limits.** Premium requests can continue through the Standard route when premium capacity is temporarily unavailable.
