#!/usr/bin/env python3

import json
import os
import sys
import time
from pathlib import Path

import pyautogui

CONFIG_FILE = Path.home() / "Downloads" / "attendee_capture_config.json"
OUTPUT_DIR = Path.home() / "Downloads" / "attendee_profile_screenshots"

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.20


def wait_for_enter(message: str) -> None:
    input(f"\n{message}\nPress Enter when ready...")


def capture_mouse_position(label: str) -> dict:
    wait_for_enter(f"Move your mouse to: {label}")
    x, y = pyautogui.position()
    print(f"{label}: ({x}, {y})")
    return {"x": x, "y": y}


def capture_region() -> dict:
    print("\nWe will capture the screenshot region in 2 steps.")
    tl = capture_mouse_position("TOP-LEFT corner of the attendee profile screenshot region")
    br = capture_mouse_position("BOTTOM-RIGHT corner of the attendee profile screenshot region")

    left = min(tl["x"], br["x"])
    top = min(tl["y"], br["y"])
    width = abs(br["x"] - tl["x"])
    height = abs(br["y"] - tl["y"])

    if width <= 0 or height <= 0:
        raise ValueError("Invalid screenshot region.")

    region = {"left": left, "top": top, "width": width, "height": height}
    print(f"Screenshot region: {region}")
    return region


def save_config(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"\nSaved config to {CONFIG_FILE}")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"Config file not found: {CONFIG_FILE}")
        print("Run: python3 iphone_attendee_screenshot_capture.py calibrate")
        sys.exit(1)

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def next_image_index(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    nums = []
    for f in output_dir.glob("*.png"):
        if f.stem.isdigit():
            nums.append(int(f.stem))
    return max(nums) + 1 if nums else 1


def click_point(point: dict) -> None:
    pyautogui.click(point["x"], point["y"])


def capture_contact(index: int, region: dict, output_dir: Path) -> Path:
    filename = output_dir / f"{index:05d}.png"
    shot = pyautogui.screenshot(region=(
        region["left"],
        region["top"],
        region["width"],
        region["height"],
    ))
    shot.save(filename)
    return filename


def calibrate() -> None:
    print("Calibration starting.")
    print("Do not move or resize the iPhone Mirroring window after calibration.")

    rows_raw = input("\nHow many visible rows do you want to calibrate per batch? [default 7]: ").strip()
    visible_rows = int(rows_raw) if rows_raw else 7

    print("\nSTEP 1: Go to the attendee LIST screen.")
    print("Capture each visible row exactly, one by one.")
    row_points = []
    for i in range(visible_rows):
        row_points.append(capture_mouse_position(f"CENTER of visible attendee row {i + 1}"))

    print("\nSTEP 2: Open any attendee CONTACT profile.")
    back_button = capture_mouse_position("BACK button on the open attendee profile")
    screenshot_region = capture_region()

    open_delay_raw = input("\nDelay after opening a contact in seconds? [default 1.8]: ").strip()
    open_delay = float(open_delay_raw) if open_delay_raw else 1.8

    back_delay_raw = input("Delay after going back in seconds? [default 1.1]: ").strip()
    back_delay = float(back_delay_raw) if back_delay_raw else 1.1

    settle_delay_raw = input("Small settle delay between actions? [default 0.4]: ").strip()
    settle_delay = float(settle_delay_raw) if settle_delay_raw else 0.4

    config = {
        "row_points": row_points,
        "visible_rows": visible_rows,
        "back_button": back_button,
        "screenshot_region": screenshot_region,
        "open_delay": open_delay,
        "back_delay": back_delay,
        "settle_delay": settle_delay,
        "output_dir": str(OUTPUT_DIR),
    }

    save_config(config)
    print("\nCalibration done.")
    print("Run with:")
    print("python3 iphone_attendee_screenshot_capture.py run")


def run_capture() -> None:
    config = load_config()
    output_dir = Path(config.get("output_dir", str(OUTPUT_DIR))).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    current_index = next_image_index(output_dir)
    row_points = config["row_points"]
    back_button = config["back_button"]
    screenshot_region = config["screenshot_region"]
    open_delay = config["open_delay"]
    back_delay = config["back_delay"]
    settle_delay = config["settle_delay"]

    print("\nImportant:")
    print("Start each batch on an OPEN CONTACT screen.")
    print("The script will press Back first, then work through the saved list rows.")
    print("Move mouse to the top-left corner of the screen to abort.")
    time.sleep(2)

    batch_number = 1

    while True:
        input(f"\nBatch {batch_number}: open any attendee contact screen now, then press Enter to begin this batch.")

        click_point(back_button)
        time.sleep(back_delay)

        for idx, row_point in enumerate(row_points, start=1):
            print(f"Row {idx}")
            click_point(row_point)
            time.sleep(open_delay)

            saved = capture_contact(current_index, screenshot_region, output_dir)
            print(f"  saved {saved.name}")
            current_index += 1

            time.sleep(settle_delay)
            click_point(back_button)
            time.sleep(back_delay)

        user_input = input(
            "\nTwo-finger scroll the attendee list to the next chunk, then open any contact in that new chunk.\n"
            "Press Enter to continue, or type q to stop: "
        ).strip().lower()

        if user_input == "q":
            break

        batch_number += 1

    print("\nDone.")
    print(f"Saved in: {output_dir.resolve()}")


def usage() -> None:
    print("Usage:")
    print("  python3 iphone_attendee_screenshot_capture.py calibrate")
    print("  python3 iphone_attendee_screenshot_capture.py run")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        usage()

    command = sys.argv[1].lower()

    if command == "calibrate":
        calibrate()
    elif command == "run":
        run_capture()
    else:
        usage()


if __name__ == "__main__":
    main()
