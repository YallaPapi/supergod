# Marketing Machine Execution SOPs (Geelark + Reddit + Instagram)

## Objective
Build a repeatable service-delivery engine for content creation + posting that gets to the first 10 paying clients fast while staying platform-compliant.

## Scope
This SOP covers:
- Client onboarding
- Content production
- Client approvals
- Posting automation (Geelark, Reddit, Instagram)
- Reporting
- Client handoff/renewal

## Tooling Stack
- i2v content system: content ideation, script/asset generation, repurposing
- Geelark posting automation: scheduled Instagram posting and account-level operations
- Reddit poster/warmup tools: account warming, post scheduling support, queue tracking
- Instagram posting infra: final publish path, comment/reply workflow, analytics extraction
- Shared workspace (Notion/Airtable/Sheet + cloud drive): source of truth for queue, approvals, and reporting

## Roles and Ownership
- Growth Operator (owner): onboarding, offer fit, approvals follow-up, weekly reporting
- Content Operator: brief creation, i2v production, QA, revisions
- Distribution Operator: Geelark scheduling, Reddit queue execution, live checks, escalation
- Account Lead: client communication, renewal/handoff, upsell

## Stage 1: Client Onboarding SOP (Day 0 to Day 2)

### Inputs required before kickoff
- Offer selected (example packages):
  - Starter: 12 posts/month (IG + Reddit repurpose)
  - Standard: 20 posts/month + weekly report
  - Growth: 30 posts/month + engagement support
- Signed agreement + payment method on file
- Access checklist complete:
  - Instagram credentials/session access path
  - Geelark profile assigned
  - Brand assets (logo, colors, offer docs, testimonials)
  - 3 competitor accounts + 3 reference creators

### Onboarding checklist
1. Create client workspace folder and tracker row.
2. Run 30-minute intake call using fixed template:
   - ICP, offer, pricing, objections, proof assets, CTA, compliance constraints.
3. Define content pillars (3 to 5), CTA map, and posting cadence.
4. Build first 2-week content calendar draft in tracker.
5. Set SLA expectations in writing:
   - Draft turnaround: 48 hours
   - Client approval window: 24 hours
   - Publishing SLA after approval: same day
6. Get explicit approval on tone, claims boundaries, and escalation contact.

### Output of Stage 1
- Approved strategy one-pager
- 2-week draft calendar
- Access + credentials validated
- First production sprint ready

## Stage 2: Content Production SOP (Weekly)

### Weekly capacity target (fast path to 10 clients)
- One pod handles 5 clients at Standard package level.
- For first 10 clients: run 2 pods with shared QA.

### Production workflow (repeat every week)
1. Monday planning (60 min/client):
   - Pull last week performance metrics.
   - Select next batch topics by pillar and funnel stage.
2. Create batch briefs in i2v:
   - 5 hooks
   - 5 short-form scripts/caption cores
   - 5 CTA variants tied to one offer action.
3. Generate/edit assets:
   - Produce post assets and captions.
   - Repurpose each core idea for Instagram and Reddit format.
4. QA pass (required before approval):
   - Brand voice match
   - Claim/substantiation check
   - CTA clarity
   - Platform compliance check
5. Move approved internal drafts to client approval queue.

### Definition of done for each content unit
- Platform-ready copy + media
- CTA mapped to one conversion action
- Tagged by pillar, funnel stage, and objective (reach, engagement, lead)

## Stage 3: Client Approval SOP (24-hour cycle)

### Approval mechanism
- Send one daily batched approval packet (not one-by-one requests).
- Include simple choices: `Approve`, `Revise`, `Hold`.
- Require revision comments in one message block to avoid piecemeal loops.

### SLA and escalation
1. Send approval packet by 12:00 local client time.
2. If no response by +8 hours, send reminder.
3. If no response by +24 hours, apply pre-agreed default:
   - Either `auto-approve evergreen` OR `roll forward to next slot` (decide during onboarding).
4. If `Revise`, complete one revision pass within 24 hours.

### Quality gate before scheduling
- No scheduling without explicit status in tracker: `APPROVED`.
- Exception path allowed only if onboarding contract includes auto-approval rule.

## Stage 4: Posting Automation SOP (Geelark + Reddit + Instagram)

### A) Instagram scheduling via Geelark
1. Load approved assets/captions into queue with publication timestamps.
2. Enforce spacing rules:
   - Minimum 3 hours between feed posts per account.
   - Stagger Stories/Reels cadence to mimic normal operator behavior.
3. Preflight check before each publish block:
   - Account session valid
   - Media renders correctly
   - Caption/hashtags/links intact
4. Live publish verification:
   - Confirm post URL exists
   - Confirm caption integrity
   - Log publish timestamp and post ID in tracker
5. Failure handling:
   - Retry once after 10 minutes.
   - If second fail, switch to manual publish path and flag account health.

### B) Reddit posting + warmup operations
1. Maintain subreddit matrix per client:
   - Allowed topics
   - Promotional tolerance
   - Karma/account-age expectations
   - Banned content patterns
2. Warmup protocol for newer accounts:
   - Days 1 to 7: comments only, no links
   - Days 8 to 14: low-frequency posts in high-fit communities
   - Day 15+: introduce value-first posts with soft CTA
3. Posting rules per account:
   - Cap posting frequency to avoid repetitive/mass patterns.
   - Rotate communities and post formats.
   - Prioritize discussion-first posts over direct promotion.
4. Moderation response flow:
   - If removed: log reason, adjust community allowlist, do not repost unchanged.
   - If warned: pause account, run compliance review before resuming.

### C) Platform-compliance guardrails (must enforce)
- Reddit classifies repeated or unsolicited mass engagement as spam and disallows automation patterns that facilitate spam; avoid repetitive cross-posting and mass unsolicited actions.
- Reddit notes promotional content may be allowed in some communities, but each community can enforce stricter rules; follow each subreddit rule set before posting.
- Meta/Instagram terms prohibit creating accounts or collecting/accessing information through unauthorized automated means; keep automation limited to authorized account operations.

## Stage 5: Reporting SOP (Weekly + Monthly)

### Weekly client report (send every Monday)
Include:
- Output metrics:
  - Posts published vs planned
  - Approval turnaround time
  - On-time publish rate
- Performance metrics:
  - Reach/impressions
  - Saves/shares/comments
  - Link clicks/DM inquiries/leads
- Insight + action:
  - Top 3 winners (format + topic + hook)
  - Bottom 3 underperformers with next test
  - Next week test plan (3 tests max)

### Internal ops report (for service quality)
Track per client:
- Production cycle time
- Approval delay rate
- Publish failure rate
- Compliance incidents (removals/warnings)
- Lead response time to inbound inquiries

## Stage 6: Handoff / Renewal SOP (End of Month 1 and every 30 days)

### Handoff package contents
- Content performance summary
- What to keep / stop / start
- Next 30-day calendar draft
- Asset library export (captions, media, hooks)
- Platform account health summary

### Renewal workflow
1. 7 days before renewal: send performance summary + next-cycle plan.
2. Offer one expansion option only (reduce decision friction):
   - Add posting volume OR add engagement handling.
3. Confirm renewal at least 48 hours before billing date.

## Fastest Path Playbook: First 10 Paying Clients

### Execution cadence (first 30 days)
- Daily outbound target:
  - 30 direct outreach touches/day (founders, creators, service businesses)
  - 10 warm Reddit interactions/day in target communities
  - 1 proof-style Instagram post/day on your own brand account
- Sales motion:
  - Offer a 14-day pilot with fixed deliverables and clear success metrics.
  - Start with one niche only until 10 clients are closed.
- Delivery protection:
  - Do not exceed 5 active clients per delivery pod without adding another operator.

### Core KPIs to hit
- Lead to call booking rate
- Call close rate
- Time-to-first-draft (target <= 48h)
- On-time publish rate (target >= 95%)
- Client month-1 retention

## Source-backed policy references
- Reddit Help, "Spam" (updated October 9, 2025): https://support.reddithelp.com/hc/en-us/articles/360043504051-Spam
- Reddit Help, "How do I keep spam out of my community?" (updated August 15, 2025): https://support.reddithelp.com/hc/en-us/articles/28012014962580-How-do-I-keep-spam-out-of-my-community
- Meta Terms summary (Instagram/Facebook Help Center): https://www.facebook.com/help/581066165581870/

## Notes for operators
- If a client asks for aggressive automation that risks policy violations, escalate before execution.
- Keep all approval, post, and incident logs in one tracker so any worker can take over same day.
