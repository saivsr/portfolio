# Prompt 06 — Derive Producer-Focused CSV

**Stage:** 2 (people flow)
**Input:** project-level JSON from Prompt 05 (with `people` array populated)
**Output:** strict CSV, one row per producer, with plausible person-email matching

---

Convert the extracted JSON into a clean producer-focused CSV.

Return only CSV.
No markdown.
No explanation.
No extra text.

## Column order

```
full_name,first_name,last_name,company_name,role,project_title,project_descriptor,status,project_shoot_location,person_email_1,person_email_2,generic_email_1,generic_email_2,generic_email_3
```

## Row logic

1. One row = one producer.
2. Keep only rows where `role` is `PRODUCER` or `WRITER/PRODUCER`.
3. Assign exactly one company to each producer.
4. Choose the producer's company using this exact ranking:
   a. A `production_company` whose email block contains a plausible person-email match to that producer
   b. A `production_company` with `relationship_to_project = primary_listing_entity`
   c. Any other `production_company`
   d. If no `production_company` exists, the first listed company
5. If the project has no companies at all, keep the row with `company_name` blank and no emails.
6. Do not output multiple companies for the same producer in one row.
7. Remove exact duplicate rows.

## Name rules

- `full_name` must contain one person only.
- Split `full_name` into `first_name` and `last_name`.
- If there is a middle name or initial, keep it in `first_name`. Example: `R. Scott Gemmill` → first=`R. Scott`, last=`Gemmill`.
- Treat `Jr.`, `Sr.`, `II`, `III`, `IV` as suffixes attached to `last_name`. Example: `Robert Guza Jr.` → first=`Robert`, last=`Guza Jr.`. If the source uses `Last, Jr.` style, normalize to `Last Jr.` before splitting.
- If `full_name` contains `, ` separating what appears to be two people and the token after the comma is **not** a suffix, split into separate rows.
- Do not output brackets, quotes, or list syntax.
- Do not merge two people into one row.

## Person-email rules

- Only use emails from the selected company block for `person_email_*`.
- A person email must be non-generic and plausibly match the producer's name.
- A plausible match means the local part clearly maps to the producer by one of these patterns:
  1. first name
  2. last name
  3. first initial + last name
  4. initials that clearly correspond to the producer's full name
- The matched name component (first or last) must be at least 3 characters long.
- Generic inboxes are never person emails.
- If the email does not clearly match the producer, leave `person_email` blank.
- If two emails both plausibly match, place them in `person_email_1` and `person_email_2` in listed order.
- If ownership is uncertain, leave the `person_email` fields blank.
- Do not guess.

## Generic-email rules

Generic emails are inbox-style addresses whose local part **begins with** one of these prefixes: `info`, `admin`, `contact`, `hello`, `office`, `team`, `production`, `productions`, `general`, `mail`, `inquiries`, `enquiry`, `enquiries`.

- Put up to three generic emails from the selected company block into `generic_email_1` through `generic_email_3` in listed order.
- Do not combine multiple emails into one cell.
- Do not use separators such as `|` or `;` inside a cell.
- Do not place non-generic emails into `generic_email_*`.

## Exclusion rules

- Do not output non-generic emails that do not plausibly belong to the producer.
- Do not output project-level mixed email blobs.
- Do not include cast, directors, or other non-producer roles.
- Do not duplicate a producer because multiple companies exist. Use the company-selection rules above and keep one row only.

## Formatting rules

- No list syntax inside cells.
- No brackets.
- No quoted Python-style arrays.
- Treat `N/A`, `NA`, `none`, `null`, `unknown`, and `-` as blank.
- Leave missing values blank.
- Output strict CSV only.
