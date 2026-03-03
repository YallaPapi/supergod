# Paid Pilot Conversion Funnel (Minimal, Launch-in-1-Day)

## Objective
Get first 10 paying pilot clients for done-for-you content creation + posting services using a minimal funnel:
1. Landing page
2. Calendly fit call
3. Short intake form
4. Deposit payment option

## Why this funnel shape
- Short path beats complex funnels in early-stage service sales: fewer steps to a paid commitment.
- Dual CTA (`Book Call` + `Reserve with Deposit`) captures both consultative and high-intent buyers.
- Paid pilot de-risks full retainer while filtering out low-intent leads.

## Offer definition (use this as-is)
- Offer name: `30-Day Content Engine Pilot`
- Deliverables: `12 short-form videos + 20 native posts + publishing + weekly metrics summary`
- Timeline: `7-day first batch delivery, 30-day execution window`
- Price: `$2,000 pilot`
- Payment structure: `$600 non-refundable slot deposit` + `$1,400 at kickoff`
- Risk reversal: `If no kickoff within 14 days due to our delay, full deposit refund`

## Funnel architecture
- `Traffic`: outbound DMs/email, Reddit posts, Instagram CTAs
- `Landing page`: one-page proof + CTA + FAQ (use template file)
- `Calendly`: 15-minute Fit Call event
- `Intake form`: 6 mandatory fields, completion target <5 minutes
- `Payment`: Stripe payment link for deposit
- `Follow-up`: auto-email + manual 24h reminder

## Assets created
- Landing page template: [landing_page_template.html](/workspace/w2/.supergod-worktrees/94e9f31f7d48-mm-03-funnel/docs/marketing_machine/landing_page_template.html)

## Launch checklist (90 minutes)
1. Duplicate and edit [landing_page_template.html](/workspace/w2/.supergod-worktrees/94e9f31f7d48-mm-03-funnel/docs/marketing_machine/landing_page_template.html).
2. Replace `YOUR_HANDLE` and `YOUR_DEPOSIT_LINK` with real URLs.
3. Publish on your fastest stack (`Carrd`, `Webflow`, `Framer`, or static host).
4. Create Calendly event: `Pilot Fit Call (15 min)`.
5. Calendly settings:
- Redirect after booking to your intake form URL.
- Add UTM passthrough in event description.
- Add one SMS/email reminder 2h before call.
6. Create intake form (Typeform/Tally/Google Form) with fields below.
7. Create Stripe payment link for `$600` deposit.
8. Add both links in top CTA and sticky CTA.
9. Test full flow end-to-end on mobile and desktop.

## Intake form (copy/paste)
Title: `Content Engine Pilot Intake`

Fields:
1. `Business name + website`
2. `Main offer and average deal size`
3. `Primary audience (who buys)`
4. `Current monthly inbound leads`
5. `Content channels currently active`
6. `Biggest bottleneck (strategy, production, posting, consistency)`
7. `Asset access: logo, brand guide, existing footage`
8. `Decision-maker confirmation (yes/no)`

Rule:
- Required fields: 1-6 and 8
- Conditional logic: if no decision-maker, auto-route to nurture sequence instead of sales call

## CTA copy blocks
Primary CTA:
- `Book a 15-min Fit Call`

Secondary CTA:
- `Skip Call: Reserve with Deposit`

Urgency line:
- `Only 10 pilot slots per month to maintain production quality.`

Guarantee line:
- `Deposit is credited to pilot and refunded if we are not a fit.`

## Instrumentation (minimum viable analytics)
Track these events in GA4, PostHog, or simple server logs:
1. `lp_view`
2. `cta_book_call_click`
3. `cta_deposit_click`
4. `calendly_booking_completed`
5. `intake_submitted`
6. `deposit_paid`
7. `pilot_closed`

UTM standard:
- `utm_source`: reddit | instagram | outbound_email | outbound_dm
- `utm_medium`: post | bio | comment | cold_dm
- `utm_campaign`: paid_pilot_mar2026

## Conversion targets for first 10 clients
Use these operator benchmarks to diagnose bottlenecks fast:
- Landing page view -> call booked: `>= 4%`
- Landing page view -> deposit click: `>= 1.5%`
- Call booked -> show rate: `>= 70%`
- Calls attended -> pilot close: `>= 25%`

At these rates, target ~`570 landing views` to close first `10 pilots`.

## 7-day execution sprint
1. Day 1: launch page + calendly + form + payment link.
2. Day 2: run 50 outbound messages with landing link and personalized opener.
3. Day 3: post 1 proof-driven Reddit thread + 1 Instagram carousel with CTA in caption.
4. Day 4: follow up all non-responders from days 2-3.
5. Day 5: review funnel metrics, rewrite headline/CTA if booking rate <4%.
6. Day 6: run second outbound batch (50 messages) using improved copy.
7. Day 7: close warm leads and collect first 1-3 deposits.

## Immediate outbound line to feed the funnel
`Built a 30-day done-for-you content pilot for service businesses: we handle creation + posting, you handle closing. If useful, here is the exact scope + pricing page: [link].`

## Handoff to operations once deposit is paid
1. Auto-send intake form + kickoff scheduler.
2. Create client workspace and content board.
3. Confirm first 7-day deliverable date in writing.
4. Start production pipeline within 24 hours.
