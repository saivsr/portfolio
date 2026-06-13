# Sai Vsr — Portfolio

**Product · AI · GTM · Content · Operations.** I build the systems behind go-to-market teams and write the words that move through them.

The folders below are production automation and data-extraction tools — scraping, OCR, LLM pipelines, internal tooling, and Slack/PDF ops. Each is a self-contained project with its own README.

> Client data and credentials are replaced with fictional placeholders. The architecture, logic, and engineering decisions are real; only sensitive values are stubbed.

## Projects

| Project | What it does | Stack |
|---|---|---|
| [Live Outbound Dashboard](./Live%20Outbound%20Dashboard) | Rebuilds a recruiting firm's cold-email funnel from four systems that disagree on every number into a daily, self-reconciling dashboard where every figure traces back to its source. | Python · Postgres · Next.js · GitHub Actions |
| [Pixels to Prospects](./Pixels%20to%20Prospects) | Extracts a clean prospect list from an event app with no export and no API: capture → OCR triage → Claude normalization → dedup. | Python · Tesseract OCR · Claude |
| [FDA 510(k) Scraper](./FDA%20510K%20Scraper) | Turns the FDA medical-device clearance database into structured, queryable data for health-tech targeting. | Python |
| [IMDbPro Company Scraper](./IMDbPro%20Company%20Scraper) | Pulls structured company data from IMDbPro. | Python |
| [Client Briefing Automation](./Client%20Briefing%20Automation) | Generates client briefing documents from source inputs. | Python |
| [Low Performing Campaign Automation](./Low%20Performing%20Campaign%20Automation) | Flags and routes underperforming campaigns. | Python |
| [Multi-Workspace Campaign Launch Alerts](./Multi-Workspace%20Campaign%20Launch%20Alerts) | Slack alerts the moment a campaign goes live across every sending workspace. | Python · Slack |
| [No-Show Slack Automation](./No-Show%20Slack%20Automation) | Flags meeting no-shows to Slack in real time. | Python · Slack |
| [Production Weekly PDF Pipeline](./Production%20Weekly%20PDF%20Pipeline) | Generates a weekly PDF report from production data. | Python |
| [Slack Availability Checker](./Slack%20Availability%20Checker) | Checks and reports team availability via Slack. | Python · Slack |

> If a project link 404s, the folder name just differs slightly — fix the path in the link (spaces become `%20`).

## Writing

Published bylines as **Sai Vsr** across [BGR](https://www.bgr.com/author/saivsr/) (Static Media), [TheGamer](https://www.thegamer.com/author/sai-vsr/), and [Game Rant](https://gamerant.com/author/sai-vsr/) (Valnet) — AI, consumer tech, gaming, and product coverage. Also a published singer-songwriter (Rolling Stone, Indie Music Diaries).

Full clips → **[Writing Samples »](./Writing%20Samples)**

## Stack

`Python` · `TypeScript` · `Next.js` · `Supabase / Postgres` · `n8n` · `Clay` · `Claude / Claude Code` · `GitHub Actions` · `Vercel`

---
📫 **[LinkedIn](https://www.linkedin.com/in/sai-vsr/)** · siri.rangasai@gmail.com
