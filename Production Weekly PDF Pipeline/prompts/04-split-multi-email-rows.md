# Prompt 04 — Multi-Email Row Splitter

**Stage:** 4 (optional, company flow)
**Input:** CSV where one row may contain multiple comma-separated emails in a single email field
**Output:** CSV where each row contains exactly one email address

---

You are given a CSV file.

Your task is to create a new CSV where each row contains exactly one email address.

## Rules

1. Preserve all original data.
2. Ignore completely empty rows from the original CSV.
3. Do not create any empty rows in the new CSV.
4. If a row contains multiple email addresses in the email field, separated by commas, split them into separate rows.
5. For split rows, duplicate all the other column values exactly as they appeared in the original row.
6. If a row contains only one email address, keep it as one row.
7. Trim extra spaces around email addresses after splitting.
8. Do not remove, rewrite, or normalize any valid data other than splitting multiple emails into separate rows.
9. Keep the same column headers in the output CSV.
10. Return the final result as a clean CSV file.

## Important

- Do not lose any email addresses.
- Do not merge rows.
- Do not invent or guess missing values.
- Only exclude rows that are completely empty.
