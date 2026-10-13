#!/usr/bin/env python3
"""
Utility script to generate optimized, correctly cropped background images in data/backgrounds/temp/
based on slide configuration in data/slides.json.

Canvas aspect ratio: 390 : 845 (~9:19.5), rendered at high-resolution 1080 x 2340 px.
"""

import os
import sys
import json
import argparse
from PIL import Image, ImageOps

TARGET_W, TARGET_H = 1080, 2340
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLIDES_PATH = os.path.join(BASE_DIR, 'data', 'slides.json')
BG_DIR = os.path.join(BASE_DIR, 'data', 'backgrounds')
TEMP_DIR = os.path.join(BG_DIR, 'temp')


def crop_image(bg_filename, bg_x=0.0, bg_y=0.0, bg_zoom=100.0, quality=90):
    bg_clean = os.path.basename(bg_filename.replace('temp/', ''))
    src_path = os.path.join(BG_DIR, bg_clean)
    if not os.path.exists(src_path):
        print(f"⚠️ Warning: Source background not found: {src_path}")
        return False

    os.makedirs(TEMP_DIR, exist_ok=True)
    dest_path = os.path.join(TEMP_DIR, bg_clean)

    orig = Image.open(src_path)
    orig = ImageOps.exif_transpose(orig).convert('RGB')
    im_w, im_h = orig.size

    # Scale factor to cover target canvas (object-fit: cover equivalent)
    s_factor = max(TARGET_W / im_w, TARGET_H / im_h)
    z = (bg_zoom if bg_zoom is not None else 100.0) / 100.0
    dx = ((bg_x if bg_x is not None else 0.0) / 100.0) * TARGET_W
    dy = ((bg_y if bg_y is not None else 0.0) / 100.0) * TARGET_H

    final_w = int(round(im_w * s_factor * z))
    final_h = int(round(im_h * s_factor * z))

    cx = TARGET_W / 2.0 + dx
    cy = TARGET_H / 2.0 + dy

    top_left_x = int(round(cx - final_w / 2.0))
    top_left_y = int(round(cy - final_h / 2.0))

    scaled = orig.resize((final_w, final_h), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (TARGET_W, TARGET_H), (0, 0, 0))
    canvas.paste(scaled, (top_left_x, top_left_y))

    canvas.save(dest_path, 'JPEG', quality=quality, optimize=True)
    orig_kb = os.path.getsize(src_path) / 1024
    dest_kb = os.path.getsize(dest_path) / 1024
    print(f"✅ Generated {dest_path}: {orig_kb:.0f} KB -> {dest_kb:.0f} KB (scaled {final_w}x{final_h} at offset ({top_left_x}, {top_left_y}))")
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate optimized/cropped temp slide backgrounds.")
    parser.add_argument("--slide", "-s", help="Specific slide ID to regenerate (e.g. 'portada')")
    parser.add_argument("--all", "-a", action="store_true", help="Regenerate all image backgrounds from slides.json")
    args = parser.parse_args()

    if not os.path.exists(SLIDES_PATH):
        print(f"❌ Error: slides file not found at {SLIDES_PATH}")
        sys.exit(1)

    with open(SLIDES_PATH, 'r', encoding='utf-8') as f:
        slides = json.load(f)

    target_slide_id = args.slide
    if not target_slide_id and not args.all:
        target_slide_id = "portada"

    for slide in slides:
        slide_id = slide.get("id")
        if target_slide_id and slide_id != target_slide_id:
            continue

        bg = slide.get("background", "")
        if not bg or bg.lower().endswith(('.mp4', '.webm', '.mov', '.m4v')):
            continue

        bg_x = float(slide.get("bg_x", 0.0) or 0.0)
        bg_y = float(slide.get("bg_y", 0.0) or 0.0)
        bg_zoom = float(slide.get("bg_zoom", 100.0) or 100.0)

        print(f"Processing slide '{slide_id}' ({bg}): zoom={bg_zoom}%, x={bg_x}%, y={bg_y}%")
        crop_image(bg, bg_x=bg_x, bg_y=bg_y, bg_zoom=bg_zoom)


if __name__ == '__main__':
    main()
