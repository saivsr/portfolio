// lib/utils/format.ts
// Number / currency / percent formatters used throughout the dashboard.
// All formatters are pure functions; callable from RSC.

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US").format(Math.round(n));
}

export function fmtMoney(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);
}

/** 0.0125 → "1.25%" with `digits` decimal places */
export function fmtPct(
  ratio: number | null | undefined,
  digits = 2,
): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** 0.001 → "1.00‰" — per-mille for legibility on small rates */
export function fmtPermille(
  ratio: number | null | undefined,
  digits = 2,
): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  return `${(ratio * 1000).toFixed(digits)}‰`;
}

export function fmtDate(d: string | Date | null | undefined): string {
  if (!d) return "—";
  const dt = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toISOString().slice(0, 10);
}

export function fmtDateTime(d: string | Date | null | undefined): string {
  if (!d) return "—";
  const dt = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toLocaleString("en-US", {
    timeZone: "America/New_York",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

// ── Client-safe campaign labels ────────────────────────────────────
// Internal campaign titles carry an operations archetype prefix delimited
// by a pipe, e.g. "Recruiter Scrape | Executive Search — VP Eng". The prefix
// is internal taxonomy (the tool/lane a campaign was built in) and must never
// reach the client. We strip any leading archetype segment and surface only
// the human-readable remainder. If nothing meaningful remains, fall back to a
// neutral label.
const INTERNAL_ARCHETYPE_PREFIXES = [
  "recruiter scrape",
  "master recruitment",
  "li jobs",
  "linkedin jobs",
  "plusvibe",
  "missive",
  "delphi",
  "hubspot",
];

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Remove every banned internal term ANYWHERE in the string (whole-word,
// case-insensitive), then tidy up the separators it leaves behind. This is the
// safety net behind the leading-segment logic: a banned token in a middle or
// trailing pipe-segment ("Operations | Master Recruitment | MPC") must never
// reach the client.
function scrubInternalTerms(s: string): string {
  let out = s;
  for (const term of INTERNAL_ARCHETYPE_PREFIXES) {
    out = out.replace(new RegExp(`\\b${escapeRegExp(term)}\\b`, "gi"), "");
  }
  // Collapse separators/whitespace left where terms were removed.
  out = out
    .replace(/\s*[|–—-]\s*[|–—-]\s*/g, " — ")
    .replace(/(^[\s|–—-]+)|([\s|–—-]+$)/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  return out;
}

/**
 * Turn an internal campaign name into client-safe display text.
 * - Strips a leading internal-archetype segment ("<archetype> | rest").
 * - Also strips a bare leading archetype token even without a pipe.
 * - Returns a neutral label when the result would be empty / all-internal.
 */
export function fmtCampaignLabel(
  name: string | null | undefined,
): string | null {
  if (name == null) return null;
  let s = String(name).trim();
  if (!s) return null;

  // Split on pipe and drop any leading segments that are pure internal
  // archetype tokens. Keep the first human-readable segment onward.
  if (s.includes("|")) {
    const parts = s.split("|").map((p) => p.trim());
    while (
      parts.length > 1 &&
      INTERNAL_ARCHETYPE_PREFIXES.includes(parts[0]!.toLowerCase())
    ) {
      parts.shift();
    }
    s = parts.join(" — ").trim();
  } else {
    // No pipe: strip a bare leading archetype token if the name starts with one.
    const lower = s.toLowerCase();
    for (const p of INTERNAL_ARCHETYPE_PREFIXES) {
      if (lower === p) return "Outbound campaign";
      if (lower.startsWith(p + " ")) {
        s = s.slice(p.length).trim();
        break;
      }
    }
  }

  // Clean up leftover separators/punctuation at the edges.
  s = s.replace(/^[\s|–—-]+|[\s|–—-]+$/g, "").trim();

  // Final global safety net: strip any banned term that survived in a non-
  // leading position (the leading logic only handles the first segment).
  s = scrubInternalTerms(s);

  return s.length > 0 ? s : "Outbound campaign";
}
