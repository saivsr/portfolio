# Prompt 01 — Extract Projects + Companies (JSON)

**Stage:** 1 (company flow)
**Input:** Production Weekly PDF (semi-structured)
**Output:** project-level JSON written as `partN.json` files in `/home/claude/work/`, merged into a single deliverable

---

You are extracting structured data from a semi-structured film and television production PDF such as Production Weekly.

Your job is to read the PDF from start to finish, page by page, and extract every distinct project listing exactly once.

## Goal

Create a clean project-level JSON dataset that preserves company-to-project relationships correctly and does not flatten data too early.

## Scope

Capture all companies attached to each project listing, not just production companies. Filtering will happen later.

## Critical rules

1. Process the PDF in page order from beginning to end.
2. Track every distinct project listing encountered.
3. Before finalizing the JSON, verify internally that every project listing in the PDF has been captured once and only once.
4. Do not output the checklist or verification notes.
5. Work project by project.
6. Preserve grouping correctly.
7. Do not mix emails, phones, or addresses across companies.
8. Do not merge multiple companies into one company object.
9. Do not guess. If a field is unclear, leave it blank.
10. The PDF's Location field refers to the project's shoot or production location, not company headquarters.
11. Normalize company labels into short controlled values only.
12. Output only valid JSON. No markdown. No explanation. No extra text.

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
      ]
    }
  ]
}
```

## Field rules

**project_title** — Use the exact project title as shown.

**project_descriptor** — Use the format or descriptor shown in the listing, such as Feature, Series, Limited Series, Documentary, Untitled Project. Keep it short and literal.

**status** — Preserve the listing's status text as shown.

**project_shoot_location** — Use the listing's Location field exactly as the project location.

**source_page** — Use the PDF page number where the listing appears. If a project spans multiple pages, use the first page where the listing begins.

**companies** — Include every distinct company attached to the project listing. Do not exclude studios, networks, finance companies, or agencies at this stage. One company object per company.

**company_type** — Normalize into exactly one of:
- `production_company`
- `studio_or_network`
- `sales_or_finance_company`
- `agency_or_management`
- `project_specific_entity_or_spv`
- `unknown`

**relationship_to_project** — Normalize into exactly one of:
- `primary_listing_entity`
- `associated_company`
- `distributor_or_network`
- `financier_or_sales`
- `representation`
- `unknown`

**emails** — Include only emails clearly attached to that company block. Keep them as a JSON array of strings. Do not infer or invent emails.

**phones** — Include only phone numbers clearly attached to that company block. Keep them as a JSON array of strings.

**address_raw** — Preserve the company address as a single raw string exactly as found, when present. Do not parse it yet.

## Output quality standard

- Every project listing captured exactly once.
- No skipped projects.
- No duplicate projects.
- No flattened company blobs.
- No mixed company contact blocks.
- Valid JSON only.

## Execution instructions

1. Work in batches. Process at most 8–10 pages (or ~40 records) per tool call. Do not attempt a single giant write.
2. Persist after every batch. Write each batch to `/home/claude/work/` as a numbered file (`part1.json`, `part2.json`, …) using a small Python script per batch, **not** inline JSON literals.
3. Before starting each batch, `ls` the work directory and skip any batch whose output file already exists. This way, if you get interrupted, resuming picks up from the last successful batch instead of starting over.
4. After the final batch, merge all parts into the single deliverable at `/mnt/user-data/outputs/`, validate it parses, and present it.
5. If you hit a token or length limit mid-batch, split that batch further and continue — never silently truncate. For any batch producing more than ~200 lines of output, write it as a `.py` script, not inline.
