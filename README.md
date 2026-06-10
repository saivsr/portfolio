# Projects

A small collection of internal tooling I designed, built, and ran in production —
shared here as work samples. Live client data, credentials, and identifying
details have been replaced with fictional placeholders throughout; the
architecture, logic, and engineering decisions are real and unchanged.

---

## [Slack Availability Bot](./slack-availability-bot)

A Slack slash command (`/avail [name] [timezone]`) that returns a teammate's or
partner's upcoming booking availability in-channel, pulling live from **both
Cal.com and Calendly** behind one unified interface. Built as a single,
dependency-free **Cloudflare Worker** after an earlier n8n version couldn't meet
Slack's 3-second response budget.

**Highlights:**
- One platform-agnostic interface over two different scheduling products
- Calendly auth via individual tokens *or* a shared org-admin token fallback
- Timezone-aware: filters business hours in the schedule owner's zone, displays
  in the requester's zone (with per-person hour overrides)
- Slot "spreading" logic to return a readable shortlist instead of a data dump
- Built within Slack's hard 3-second timeout, sub-100ms at the edge

**Stack:** Cloudflare Workers · JavaScript · Slack slash commands · Cal.com API ·
Calendly API

→ [Read the full write-up, architecture, and code](./slack-availability-bot)

---

_More projects to be added._
