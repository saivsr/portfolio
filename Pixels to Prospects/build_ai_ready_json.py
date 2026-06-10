#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path

import pytesseract
from PIL import Image

DEFAULT_KEEP_DIR = Path.home() / "Downloads" / "attendee_ocr_output" / "keep"
DEFAULT_OUT_DIR = Path.home() / "Downloads" / "attendee_ai_ready"

SECTION_HEADERS = {
    "designations",
    "topics of interest",
    "topics",
    "summary",
    "representatives",
    "employment institution",
}

UI_PATTERNS = [
    r"request to connect",
    r"meetings?$",
    r"company url",
    r"urls?$",
]

ORG_HINTS = [
    "health", "healthcare", "center", "centre", "system", "solutions", "telecare",
    "hospital", "institute", "services", "group", "care", "behavioral",
    # [SANITISED] The original list also included a handful of specific exhibitor /
    # vendor names from the target event, used as extra org-detection hints. They've
    # been removed here. In practice you'd seed this with domain-specific company
    # names from whatever event you're processing, e.g. "acme_health", "northwind".
]


def normalize_text(text: str) -> str:
    text = text.replace("\x0c", " ")
    for ch in ["‘", "’", "“", "”", "©", "@", "[", "]", "(", ")", ">", "»", "_"]:
        text = text.replace(ch, "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[^A-Za-z0-9]+", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip(" ,.-")


def clean_lines(text: str):
    return [clean_line(ln) for ln in re.split(r"[\n\r]+", text) if clean_line(ln)]


def looks_like_location(line: str) -> bool:
    lower = line.lower()
    if "united states" in lower or "france" in lower:
        return True
    return False


def looks_like_person_name(line: str) -> bool:
    if not line:
        return False
    words = line.split()
    if not (1 <= len(words) <= 5):
        return False
    bad = {"attendee", "representative", "exhibitor", "summary", "designations", "topics"}
    if any(w.lower() in bad for w in words):
        return False
    return all(re.fullmatch(r"[A-Za-z.'-]+", w) for w in words)


def looks_like_org(line: str) -> bool:
    lower = line.lower()
    return any(h in lower for h in ORG_HINTS)


def extract_booth(text: str) -> str:
    m = re.search(r"find me at\s*([A-Za-z0-9-]+)", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bbooth[:\s#-]*([A-Za-z0-9-]+)\b", text, re.I)
    return m.group(1) if m else ""


def remove_ui_lines(lines):
    keep = []
    ui = []
    for ln in lines:
        lower = ln.lower()
        if any(re.search(p, lower) for p in UI_PATTERNS):
            ui.append(ln)
        elif lower in {"e", "gj", "senos ef", "seno oe", "sane oe", "senee e", "eee ie", "sponsor"}:
            ui.append(ln)
        else:
            keep.append(ln)
    return keep, ui


def detect_subtype(text: str) -> str:
    lower = text.lower()
    if "contributor at" in lower:
        return "contributor"
    if "representative" in lower:
        return "representative"
    if "attendee" in lower:
        return "attendee"
    if "exhibitor" in lower:
        return "exhibitor"
    return "unknown"


def detect_record_kind(lines, subtype: str, text: str) -> str:
    lower = text.lower()
    first = lines[0] if lines else ""

    if "representatives" in lower or "summary" in lower:
        if "exhibitor" in lower:
            return "company"

    if subtype == "contributor":
        return "person"

    if subtype in {"attendee", "representative"}:
        return "person"

    if looks_like_person_name(first):
        return "person"

    if subtype == "exhibitor" and looks_like_org(first):
        return "company"

    if looks_like_org(first):
        return "company"

    return "unknown"


def slice_sections(lines):
    sections = {}
    current = None

    for ln in lines:
        lower = ln.lower()
        if lower in SECTION_HEADERS:
            current = lower
            sections[current] = []
            continue

        if current:
            sections[current].append(ln)

    return sections


def extract_candidate_fields(lines, text):
    subtype = detect_subtype(text)
    record_kind = detect_record_kind(lines, subtype, text)

    name = lines[0] if lines else ""
    location = ""
    for ln in lines:
        if looks_like_location(ln):
            location = ln
            break

    booth = extract_booth(text)

    candidate_title = ""
    candidate_company = ""

    if len(lines) >= 2:
        second = lines[1]
        if "," in second:
            left, right = [x.strip() for x in second.split(",", 1)]
            candidate_title = left
            candidate_company = right
        else:
            candidate_title = second

    if len(lines) >= 3 and not candidate_company:
        third = lines[2]
        if not looks_like_location(third):
            candidate_company = third

    return {
        "name": name,
        "location": location,
        "booth": booth,
        "record_kind_guess": record_kind,
        "subtype_guess": subtype,
        "candidate_title": candidate_title,
        "candidate_company": candidate_company,
    }


def build_record(path: Path):
    img = Image.open(path)
    raw_text = pytesseract.image_to_string(img)
    raw_text = normalize_text(raw_text)

    lines = clean_lines(raw_text)
    kept_lines, ui_flags = remove_ui_lines(lines)
    sections = slice_sections(kept_lines)
    candidate_fields = extract_candidate_fields(kept_lines, raw_text)

    consumed = set()
    for value in candidate_fields.values():
        if isinstance(value, str) and value:
            consumed.add(value)

    for vals in sections.values():
        for v in vals:
            consumed.add(v)

    unresolved_lines = []
    for ln in kept_lines:
        if ln not in consumed:
            unresolved_lines.append(ln)

    return {
        "source_image": path.name,
        "raw_text": raw_text,
        "clean_lines": kept_lines,
        "ui_flags": ui_flags,
        "sections": sections,
        "candidate_fields": candidate_fields,
        "unresolved_lines": unresolved_lines,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(DEFAULT_KEEP_DIR), help="Folder containing kept profile screenshots")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR), help="Folder for AI-ready JSON output")
    args = parser.parse_args()

    keep_dir = Path(args.input_dir).expanduser()
    out_dir = Path(args.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_file = out_dir / "ai_ready_records.jsonl"
    pretty_file = out_dir / "ai_ready_records_pretty.json"

    files = sorted(keep_dir.glob("*.png"))
    if not files:
        print(f"No PNG files found in {keep_dir}")
        return

    if jsonl_file.exists():
        jsonl_file.unlink()
    if pretty_file.exists():
        pretty_file.unlink()

    all_records = []

    for i, path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] {path.name}")
        try:
            rec = build_record(path)
        except Exception as e:
            rec = {
                "source_image": path.name,
                "raw_text": "",
                "clean_lines": [],
                "ui_flags": [],
                "sections": {},
                "candidate_fields": {
                    "name": "",
                    "location": "",
                    "booth": "",
                    "record_kind_guess": "unknown",
                    "subtype_guess": "unknown",
                    "candidate_title": "",
                    "candidate_company": "",
                },
                "unresolved_lines": [f"exception: {str(e)}"],
            }

        all_records.append(rec)

        with jsonl_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with pretty_file.open("w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"JSONL file:  {jsonl_file}")
    print(f"Pretty JSON: {pretty_file}")


if __name__ == "__main__":
    main()
