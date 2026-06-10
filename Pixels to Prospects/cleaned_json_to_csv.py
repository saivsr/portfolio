#!/usr/bin/env python3

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

DEFAULT_INPUT_DIR = Path.home() / "Downloads" / "attendee_ai_cleaned"
DEFAULT_OUTPUT_FILE = Path.home() / "Downloads" / "attendee_final.csv"

FIELDS = [
    "source_image",
    "record_kind",
    "subtype",
    "name",
    "credentials",
    "title",
    "company",
    "location",
    "booth",
    "session_info",
    "designations",
    "topics",
    "summary",
    "representatives",
    "urls",
    "notes",
    "raw_text",
]


def norm_key(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name", "")
                title = item.get("title", "")
                company = item.get("company", "")
                bits = [str(x).strip() for x in [name, title, company] if str(x).strip()]
                if bits:
                    parts.append(" - ".join(bits))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                text = flatten(item)
                if text:
                    parts.append(text)
        return "; ".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def load_json_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    records = []

    if path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
            elif isinstance(obj, list):
                records.extend([x for x in obj if isinstance(x, dict)])
        return records

    obj = json.loads(text)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for key in ["records", "data", "items", "results"]:
            if isinstance(obj.get(key), list):
                return [x for x in obj[key] if isinstance(x, dict)]
        return [obj]

    return []


def iter_records(input_dir: Path) -> Iterable[dict]:
    files = sorted(list(input_dir.glob("*.json")) + list(input_dir.glob("*.jsonl")))
    for path in files:
        try:
            for rec in load_json_file(path):
                rec["_source_cleaned_file"] = path.name
                yield rec
        except Exception as e:
            print(f"Skipping {path.name}: {e}")


def make_dedupe_key(rec: dict) -> str:
    kind = norm_key(flatten(rec.get("record_kind", "")))
    name = norm_key(flatten(rec.get("name", "")))
    company = norm_key(flatten(rec.get("company", "")))
    location = norm_key(flatten(rec.get("location", "")))
    booth = norm_key(flatten(rec.get("booth", "")))

    if kind == "company" and company:
        return f"company|{company}|{location}|{booth}"

    if name and company:
        return f"person|{name}|{company}"

    if name and location:
        return f"person|{name}|{location}"

    raw = norm_key(flatten(rec.get("raw_text", "")))[:300]
    if raw:
        return f"raw|{raw}"

    return f"source|{flatten(rec.get('source_image', ''))}"


def normalize_row(rec: dict) -> dict:
    row = {}
    for field in FIELDS:
        row[field] = flatten(rec.get(field, ""))
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Folder containing Claude-cleaned JSON or JSONL chunks")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_FILE), help="CSV output path")
    parser.add_argument("--no-dedupe", action="store_true", help="Disable row dedupe")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser()
    output_file = Path(args.output).expanduser()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"Input folder not found: {input_dir}")
        return

    seen = set()
    rows = []
    duplicates = 0

    for rec in iter_records(input_dir):
        key = make_dedupe_key(rec)
        if not args.no_dedupe and key in seen:
            duplicates += 1
            continue
        seen.add(key)
        rows.append(normalize_row(rec))

    with output_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print("Done.")
    print(f"Rows written: {len(rows)}")
    print(f"Duplicates skipped: {duplicates}")
    print(f"CSV: {output_file}")


if __name__ == "__main__":
    main()
