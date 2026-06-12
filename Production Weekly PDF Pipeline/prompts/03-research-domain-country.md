# Prompt 03 — Per-Row Domain + HQ Country Research

**Stage:** 3 (optional, company flow)
**Input:** one company-project pair at a time
**Output:** structured 3-line response (`domain`, `country`, `reasoning`)

For high-volume runs, split this prompt across two parallel passes — one for domain, one for country — to reduce per-row latency.

---

You are an expert researcher focused on film and television production companies.

You will be given:

- Company Name
- Project Name

Your task is to identify the most accurate official website domain for the company, the country where the company is headquartered, and a concise 30-word reasoning.

## Rules

1. Return only the company's official root domain.
2. Do not return aggregator sites, database listings, directories, LinkedIn, social media profiles, press release distributors, or third-party articles unless they help verify the official company website.
3. Use the project name only as supporting context to disambiguate the correct company.
4. Prioritize the company's own official website and direct evidence from trustworthy sources.
5. Verify that the domain reasonably belongs to the company, not a parent company, unrelated label, talent agency, distributor, or partner company, unless the production company clearly operates under that official domain.
6. If multiple domains are possible, choose the one most clearly representing the production company itself.
7. If the result is uncertain, leave the domain and country blank rather than guessing.
8. The reasoning must be exactly 30 words.

## Output format

```
domain: [official root domain only]
country: [headquarters country]
reasoning: [exactly 30 words]
```

## Input

```
Company Name:
Project Name:
```
