# LawToolBox Research — Task 1305 Spike

**Date:** 2026-06-12  
**Status:** Sales inquiry prepared; awaiting response

## Public Information Found

- **Website:** https://www.lawtoolbox.com/
- **Company:** Founded 1998, Denver, CO. Court-rules deadline calendaring + M365 matter management.
- **Coverage:** All 50 US states + federal courts (Local/State/Federal/Appellate/Bankruptcy/specialty); ~2,300 jurisdictions. Also Canada (federal + 2 provincial).
- **API Type:** REST (OAuth2 password grant)
- **API Docs:** Behind NDA/activation wall at https://api.lawtoolbox.com/api (partial endpoints exposed)
- **Public Pricing:** Direct M365 list price $22-42/user/month (volume discounts for 80+ users); 1-year minimum; annual discount ~20%. Partner pricing NOT disclosed.
- **GitHub:** [github.com/LawToolBox](https://github.com/LawToolBox) — stale 2017 API sample available

## Known API Details (from public information leak)

- **Authentication:** OAuth2 password grant, bearer token at `/api/oauth/token`, ~14-day expiry
- **Key Endpoints:** 
  - `/firms`, `/firms/{id}/users`, `/firms/{id}/subscriptions`
  - `POST /firms/{id}/users/{uid}/matters`
  - `POST .../matters/{id}/deadlines/calculate`
  - `GET /api/deadlines/modified?cutOffDate=` (rule-change polling)
- **Payload:** JSON (Name, MatterNumber, ClientName, ToolSetID, StatePostalCode)
- **Rate Limits / SLA:** None published; informal "don't load-test" guidance only
- **Integration Claim:** Partners report 1-week sprints for MVP integration

## Confirmed Competitor Usage

- **MyCase:** Live integration (all 50 states, up to 80 deadlines per trigger, 1-click calendar sync)
- **Clio:** Live integration (syncs to Clio Calendar with reminders/reports); also competes with own native "Court Rules" feature
- **Others:** Actionstep, LEAP, PracticePanther, RocketMatter, Smokeball, Centerbase, iManage, NetDocuments, InfoTrack, Soluno, Zola

## Sales Contact Information

| Method | Details |
|-|-|
| **Sales Email** | sales@lawtoolbox.com |
| **Phone (Toll Free)** | 1-888-958-6657 |
| **Phone (Direct)** | 303-759-3572 |
| **Hours** | Monday–Friday, 8:30 AM–5:00 PM MST |
| **Fax** | 1-877-471-6892 |
| **Address** | 6400 S Fiddlers Green Cir, Suite 300, Englewood, CO 80111 |
| **Alt Email (Partnerships)** | support@lawtoolbox.com |
| **Partner Pricing Page** | https://lawtoolbox.com/partner-pricing/ |

## Unknowns (to confirm with sales)

- [ ] **Pricing model** (per-call / per-month / per-user / flat license) and whether it undercuts $22-42 direct list
- [ ] **Rate limits** for the `/deadlines/calculate` endpoint
- [ ] **SLA / uptime guarantee** for the API
- [ ] **Sandbox/test environment** availability before contract
- [ ] **Contract minimums** for partners (direct is 1-year min)
- [ ] **TTM specifics** — is there a partner template beyond 2017 GitHub sample?
- [ ] **NDA process/timeline** for accessing full API docs

## Inquiry Email Sent

**To:** sales@lawtoolbox.com  
**Subject:** API Integration Inquiry — Practice Management Platform  
**Sent:** [PENDING — ready to send]  
**Expected Response:** 24-48 business hours  

Email content:
```
Hi LawToolBox Team,

We're evaluating LawToolBox for integration into our legal practice management platform (Clarity Legal). We'd like to confirm a few details about the API:

1. Pricing model — does LawToolBox offer API access via subscription (per-call, per-month, or per-user)?
2. Coverage — does the API cover all 50 U.S. states + federal courts + Canada?
3. Integration timeline — how long does a typical integration take for a web-based practice management application?
4. Availability — is the API currently available to new partners, or is there a waiting list?
5. Documentation & NDA — can you share technical API documentation? Do we need to sign an NDA first?
6. Current integrations — can you confirm the names of legal tech platforms currently integrated with LawToolBox?

We're on a sprint-based delivery cycle and would like to understand feasibility and timeline for a summer 2026 integration.

Thanks for your time. I look forward to hearing from your team.

Best regards,
[Clarity Legal Team]
```

## Next Steps

1. Send inquiry email to sales@lawtoolbox.com
2. Wait 24-48 hours for response
3. Proceed to Task 3: Evaluate Against Go/No-Go Criteria (using response data)
4. Task 4: Write Decision Memo (GO / NO-GO / MAYBE)
5. Task 5: Update TASKS.md with spike result
