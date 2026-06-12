# Prompt 05 — Extract Projects + People (JSON)

**Stage:** 1 (people flow)
**Input:** Production Weekly PDF (parsed contents already available)
**Output:** project-level JSON augmented with a `people` array filtered to producers and writer-producers

This prompt can either augment an existing extraction from Prompt 01 (recommended — preserves prior verification) or extract from scratch.

---

You already have access to the uploaded PDF and its parsed contents in this chat.

Use the parsed PDF content already available to you as the source material. Do not restate the document, do not summarize it, and do not re-explain the PDF. Your job is to extract structured data from the parsed content into a clean project-level JSON dataset.

## Goal

Create a clean project-level JSON dataset that preserves project, company, and producer relationships correctly so a producer-focused CSV can be built later without mixing contacts.

## Important

This is a people-ready extraction pass, not the final CSV.
Do not flatten early.
Do not guess.
If a field is unclear, leave it blank.

## Reuse of prior extraction

If a prior extraction of this PDF already exists in the conversation with correctly captured projects, companies, and contact blocks, you may use it as a base and only augment it with the `people` array. If no prior extraction exists, extract from scratch. Do not silently drift from a prior correct extraction.

## Execution

Work in batches of 8–10 pages. Save each batch to `/home/claude/work/` as `partN.json` via a Python script that constructs the data as dicts and calls `json.dump`. Do not serialize large JSON literals inline in tool calls. Before starting each batch, check whether its output file already exists and skip it if so. Merge all parts at the end.

## Critical rules

1. Use the parsed PDF content already available in this chat.
2. Work through the document in its original order.
3. Capture every distinct project listing exactly once.
4. Before finalizing the JSON, verify internally that every project listing present in the parsed PDF content has been captured once and only once.
5. Do not output the checklist or verification notes.
6. Work project by project.
7. Preserve grouping correctly.
8. Do not mix emails, phones, or addresses across companies.
9. Do not merge multiple people into one person object.
10. The PDF's Location field refers to the project's shoot or production location, not company headquarters.
11. Output only valid JSON. No markdown. No explanation. No extra text.

## Output schema

```json
{
  "projects": [
    {
      "project_title": "",
      "project_descriptor": "",
      "status": "",
      "project_shoot_location": "",
      "source_page": "",
      "companies": [
        {
          "company_name": "",
          "company_type": "",
          "relationship_to_project": "",
          "emails": [],
          "phones": [],
          "address_raw": ""
        }
      ],
      "people": [
        {
          "full_name": "",
          "role": ""
        }
      ]
    }
  ]
}
```

## People rules

- Include only people whose role explicitly contains `PRODUCER` or `WRITER/PRODUCER`.
- Exclude cast, directors, cinematographers, assistants, managers, and other non-producer roles.
- If a listing contains multiple producers, create one person object per producer.
- Never keep multiple names inside one field.
- Never output list-like strings such as `"['Ali Bell', 'Akiva Schaffer']"` inside a person field.
- If a listing has no producer or writer/producer credits, the `people` array must be empty (`[]`). Do not infer producers from the company name, director, or cast. Empty people arrays are expected and valid.

## Label and continuation rules for producer extraction

- A producer value ends at the next label. Labels in this PDF include: `PRODUCER`, `WRITER/PRODUCER`, `EXECUTIVE PRODUCER`, `CO-PRODUCER`, `ASSOCIATE PRODUCER`, `LINE PRODUCER`, `WRITER`, `WRITER/DIRECTOR`, `DIRECTOR`, `SHOWRUNNER`, `LP`, `UPM`, `PM`, `PC`, `1AD`, `2AD`, `DP`, `CD`, `EDITOR`, `CAST`, `STATUS`, `LOCATION`, `PHONE`, `FAX`. Treat any of these followed by `:` as a terminator.
- Producer values may wrap across two lines. If a producer line ends with a trailing ` -`, or the final name segment is a single word and the next line starts with a capitalized word, join the two lines before parsing.

## Name splitting rules

- Producer names are typically separated by ` - `. If the PDF uses `, ` between two names (and the token after the comma is **not** a suffix like `Jr.`, `Sr.`, `II`, `III`, or `IV`), split into separate people.
- Strip parenthetical annotations like `(email@example.com)` from names. Keep only the person's name.

## Company rules

- Include every distinct company attached to the project listing.
- One company object per company.
- Keep all attached companies at this stage. Filtering happens later.

Normalize `company_type` into exactly one of:
- `production_company`
- `studio_or_network`
- `sales_or_finance_company`
- `agency_or_management`
- `project_specific_entity_or_spv`
- `unknown`

Normalize `relationship_to_project` into exactly one of:
- `primary_listing_entity`
- `associated_company`
- `distributor_or_network`
- `financier_or_sales`
- `representation`
- `unknown`

## Field rules

**project_title** — Use the exact project title as shown.

**project_descriptor** — Use the format or descriptor shown in the listing, such as Feature, Series, Limited Series, Documentary, Untitled Project. Keep it short and literal.

**status** — Preserve the listing's status text as shown.

**project_shoot_location** — Use the listing's Location field exactly as the project location.

**source_page** — Use the PDF page number where the listing begins. If a project spans multiple pages, use the first page where the listing begins.

**companies.emails** — Include only emails clearly attached to that company block. Keep them as a JSON array of strings. Do not infer or invent emails.

**companies.phones** — Include only phone numbers clearly attached to that company block. Keep them as a JSON array of strings.

**companies.address_raw** — Preserve the company address as a single raw string exactly as found, when present. Do not parse it yet.

## Output quality standard

- Every project listing captured exactly once.
- No skipped projects.
- No duplicate projects.
- No flattened company blobs.
- No mixed company contact blocks.
- No multi-name person fields.
- Valid JSON only.
