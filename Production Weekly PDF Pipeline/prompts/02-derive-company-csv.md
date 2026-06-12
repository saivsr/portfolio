# Prompt 02 — Derive Project-Company CSV

**Stage:** 2 (company flow)
**Input:** project-level JSON from Prompt 01
**Output:** strict CSV (RFC 4180), one row per project-company pair

---

Convert the extracted JSON into a clean CSV focused on project-company rows.

Return only CSV.
No markdown.
No explanation.
No extra text.

## Column order

```
project_title,project_descriptor,status,project_shoot_location,company_name,company_type,relationship_to_project,generic_email_1,generic_email_2,generic_email_3,other_email_1,other_email_2,phone_1,phone_2,phone_3,address_line_1,city,state_region,postal_code,country,source_page
```

## Row logic

1. One row = one project-company pair.
2. If a project has one or more companies with `company_type = production_company`, keep only those rows for that project.
3. If a project has no `production_company`, keep exactly one fallback row using this ranking:
   a. `project_specific_entity_or_spv`
   b. `sales_or_finance_company`
   c. `studio_or_network`
   d. `agency_or_management`
   e. `unknown`
4. If multiple companies tie within the same fallback category, prefer the one with `relationship_to_project = primary_listing_entity`. If still tied, keep the first listed.
5. Projects with no companies at all are excluded.

## Email classification

Generic prefixes (case-insensitive): `info`, `admin`, `contact`, `hello`, `office`, `team`, `production`, `productions`, `general`, `mail`, `inquiries`, `enquiry`, `enquiries`.

- Classify using `startswith`, **not** exact-match. Worked examples:
  - `info@example.com` → generic
  - `info.la@example.com` → generic
  - `production.silencedvoices@gmail.com` → generic (starts with `production`)
  - `productionpinch@gmail.com` → generic (starts with `production`)
  - `careers@`, `hr@`, `jobs@`, `resumes@`, `support@` → **NOT** generic (not in prefix list); go to `other_email_*`
  - `dave.neustadter@newline.com` → NOT generic; `other_email_*`
  - `fortypointsix@gmail.com` → NOT generic; `other_email_*` (free-email domains still go to `other_email_*`)
- Up to 3 generics in `generic_email_1..3` (listed order).
- Up to 2 non-generics in `other_email_1..2` (listed order).
- Dedupe emails within a row before allocating columns.
- Drop overflow (keep earliest listed).
- One email per cell. No separators like `|` or `;` inside cells.

## Phone rules

- Split phone numbers into `phone_1` through `phone_3` in listed order. One number per cell.
- Dedupe within row. Drop overflow (keep earliest listed).

## Address parsing

- If `address_raw` contains multiple addresses separated by `;`, parse the **first** address only.
- If `address_raw` is just a URL or domain (e.g. `roughandtumblefilms.com`, `example.com/contact`), leave all address fields blank.
- Parse when the format is recognizable. Supported formats:
  - **US:** `<street>, <city>, ST ZIP` → `country = "United States"`
  - **US with missing comma** between street/city or city/state — split street from city using a street-suffix heuristic (`St`, `Ave`, `Blvd`, `Rd`, `Dr`, `Pkwy`, `Way`, `Plaza`, `Ln`, `Ct`, `Hwy`, …)
  - **Canada:** `<street>, <city>, PR A1B 2C3` → `country = "Canada"`
  - **Australia:** `<street>, <city> STATE 0000[, Australia]` → `country = "Australia"`
  - **UK:** any address containing a UK postcode pattern like `[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}` → `country = "United Kingdom"`
  - **European "postal-before-city":** `<street>, <postal> <city>, <Country>` (France, Germany, Switzerland, Finland, Spain, Portugal, Italy)
  - **Irish Eircode** (`X00 XX00`) → `country = "Ireland"`
  - **Japanese hyphenated postal** (`XXX-XXXX`) → `country = "Japan"`
- If the format is unrecognizable, put `address_raw` into `address_line_1` as-is and leave `city`, `state_region`, `postal_code`, `country` blank.
- `country` is the company's country. Never derive it from `project_shoot_location`.

## Structural cleanup (apply to every cell)

- Remove brackets: `[`, `]`, `{`, `}`
- Remove pipes: replace ` | ` with ` / `. Remove standalone `|`.
- Treat the following as blank (case-insensitive): `N/A`, `NA`, `none`, `null`, `unknown`, `-`.
- Strip leading/trailing whitespace. Collapse internal whitespace.
- `company_type` must equal one of the allowed values. Anything else → `unknown`.
- `relationship_to_project` must equal one of the allowed values. Anything else → `unknown`.
- Remove exact duplicate rows.

## Validation (run before emitting; fix and re-derive if any fail)

- Every `company_type` and `relationship_to_project` value is in the allowed set.
- No cell contains `[`, `]`, `{`, `}`, or `|`.
- Every populated `generic_email_*` begins with an allowed prefix.
- No generic email appears in an `other_email_*` cell.
- If `country` is populated, `address_line_1` is also populated.
- No exact-duplicate rows.

## Output

- Strict CSV, RFC 4180 quoting.
- No commentary before or after.
