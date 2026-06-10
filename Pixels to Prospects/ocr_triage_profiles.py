#!/usr/bin/env python3

import argparse
import json
import re
import shutil
from pathlib import Path

import pytesseract
from PIL import Image, ImageFilter, ImageStat

DEFAULT_INPUT_DIR = Path.home() / "Downloads" / "attendee_profile_screenshots"
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "attendee_ocr_output"

JUNK_TERMS = {
    "all attendees", "filters", "sort", "my event", "agenda", "floor plan",
    "sponsors", "search", "browse attendees", "browse", "menu", "home"
}

PROFILE_TERMS = {
    "united states", "representative", "attendee", "chief", "director", "manager",
    "vice president", "behavioral", "health", "partnerships", "operations",
    "leadership", "technology", "solutions", "strategy", "growth"
}


def normalize_text(text: str) -> str:
    text = text.replace("\x0c", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def get_ocr_data(img: Image.Image):
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = []
    confidences = []

    n = len(data["text"])
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except Exception:
            conf = -1.0

        if txt:
            words.append(txt)
        if conf >= 0:
            confidences.append(conf)

    text = " ".join(words)
    text = normalize_text(text)
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return text, avg_conf


def image_stats(img: Image.Image):
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    mean = stat.mean[0]
    stddev = stat.stddev[0]

    small = gray.resize((200, 200))
    edges = small.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    edge_mean = edge_stat.mean[0]

    return {
        "gray_mean": mean,
        "gray_stddev": stddev,
        "edge_mean": edge_mean,
    }


def score_text(text: str):
    lower = text.lower()
    junk_hits = sum(1 for term in JUNK_TERMS if term in lower)
    profile_hits = sum(1 for term in PROFILE_TERMS if term in lower)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines and text:
        lines = [seg.strip() for seg in re.split(r"(?<=[a-z])(?=[A-Z])", text) if seg.strip()]

    return {
        "junk_hits": junk_hits,
        "profile_hits": profile_hits,
        "line_count": len(lines),
        "char_count": len(text),
        "has_us_location": "united states" in lower,
        "has_role_word": any(x in lower for x in ["director", "manager", "chief", "vice president", "vp"]),
        "has_person_word": any(x in lower for x in ["attendee", "representative"]),
        "has_list_ui": any(x in lower for x in ["all attendees", "filters", "sort", "search"]),
    }


def classify_record(stats, text_scores, avg_conf):
    mean = stats["gray_mean"]
    stddev = stats["gray_stddev"]
    edge_mean = stats["edge_mean"]

    junk_hits = text_scores["junk_hits"]
    profile_hits = text_scores["profile_hits"]
    char_count = text_scores["char_count"]
    line_count = text_scores["line_count"]
    has_us_location = text_scores["has_us_location"]
    has_role_word = text_scores["has_role_word"]
    has_person_word = text_scores["has_person_word"]
    has_list_ui = text_scores["has_list_ui"]

    if mean >= 247 and stddev < 8 and char_count < 20:
        return "junk", "mostly_white_blank"

    if char_count < 12 and avg_conf < 25:
        return "junk", "too_little_text"

    if has_list_ui and junk_hits >= 2 and profile_hits == 0:
        return "junk", "clear_list_or_menu"

    if junk_hits >= 3 and profile_hits <= 1:
        return "junk", "ui_heavy_text"

    strong_profile = 0
    if profile_hits >= 3:
        strong_profile += 1
    if has_us_location:
        strong_profile += 1
    if has_role_word or has_person_word:
        strong_profile += 1
    if char_count >= 60:
        strong_profile += 1
    if line_count >= 3:
        strong_profile += 1
    if avg_conf >= 35:
        strong_profile += 1
    if edge_mean >= 8:
        strong_profile += 1

    if strong_profile >= 4 and junk_hits <= 1:
        return "keep", "strong_profile_signals"

    weak_or_mixed = 0
    if char_count >= 20:
        weak_or_mixed += 1
    if line_count >= 2:
        weak_or_mixed += 1
    if profile_hits >= 1:
        weak_or_mixed += 1
    if edge_mean >= 5:
        weak_or_mixed += 1

    if weak_or_mixed >= 2:
        return "review", "ambiguous_or_partial_profile"

    return "junk", "low_signal"


def write_jsonl(path: Path, record: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def copy_to_bucket(src: Path, bucket_dir: Path):
    shutil.copy2(src, bucket_dir / src.name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Folder containing screenshot PNGs")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Folder for triage output")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser()

    keep_dir = output_dir / "keep"
    review_dir = output_dir / "review"
    junk_dir = output_dir / "junk"

    raw_file = output_dir / "ocr_raw.jsonl"
    keep_file = output_dir / "keep.jsonl"
    review_file = output_dir / "review.jsonl"
    junk_file = output_dir / "junk.jsonl"

    for d in [output_dir, keep_dir, review_dir, junk_dir]:
        d.mkdir(parents=True, exist_ok=True)

    images = sorted(input_dir.glob("*.png"))
    if not images:
        print(f"No PNG files found in {input_dir}")
        return

    for f in [raw_file, keep_file, review_file, junk_file]:
        if f.exists():
            f.unlink()

    counts = {"keep": 0, "review": 0, "junk": 0}

    for i, path in enumerate(images, start=1):
        print(f"[{i}/{len(images)}] {path.name}")
        try:
            img = Image.open(path)
            stats = image_stats(img)
            text, avg_conf = get_ocr_data(img)
            text_scores = score_text(text)
            bucket, reason = classify_record(stats, text_scores, avg_conf)

            rec = {
                "source_image": path.name,
                "bucket": bucket,
                "reason": reason,
                "avg_conf": round(avg_conf, 2),
                "gray_mean": round(stats["gray_mean"], 2),
                "gray_stddev": round(stats["gray_stddev"], 2),
                "edge_mean": round(stats["edge_mean"], 2),
                "char_count": text_scores["char_count"],
                "line_count": text_scores["line_count"],
                "junk_hits": text_scores["junk_hits"],
                "profile_hits": text_scores["profile_hits"],
                "raw_text": text,
            }

            write_jsonl(raw_file, rec)

            if bucket == "keep":
                write_jsonl(keep_file, rec)
                copy_to_bucket(path, keep_dir)
            elif bucket == "review":
                write_jsonl(review_file, rec)
                copy_to_bucket(path, review_dir)
            else:
                write_jsonl(junk_file, rec)
                copy_to_bucket(path, junk_dir)

            counts[bucket] += 1

        except Exception as e:
            rec = {
                "source_image": path.name,
                "bucket": "review",
                "reason": f"exception:{str(e)}",
                "raw_text": "",
            }
            write_jsonl(raw_file, rec)
            write_jsonl(review_file, rec)
            copy_to_bucket(path, review_dir)
            counts["review"] += 1

    print("\nDone.")
    print(f"Keep:  {counts['keep']} -> {keep_dir}")
    print(f"Review:{counts['review']} -> {review_dir}")
    print(f"Junk:  {counts['junk']} -> {junk_dir}")
    print(f"Raw JSONL:    {raw_file}")
    print(f"Keep JSONL:   {keep_file}")
    print(f"Review JSONL: {review_file}")
    print(f"Junk JSONL:   {junk_file}")


if __name__ == "__main__":
    main()
