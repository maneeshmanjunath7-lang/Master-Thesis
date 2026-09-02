#!/usr/bin/env python3
"""Create numbered contact sheets from pdftoppm-rendered page images."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: make_pdf_contact_sheets.py PAGE_IMAGE_DIRECTORY")
    source = Path(sys.argv[1])
    pages = sorted(source.glob("page-*.png"))
    if not pages:
        raise SystemExit("no page-*.png files found")
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 22)
    columns, rows, thumb_w, margin = 4, 4, 360, 24
    for batch_index in range(0, len(pages), columns * rows):
        batch = pages[batch_index : batch_index + columns * rows]
        thumbs = []
        for page_no, path in enumerate(batch, start=batch_index + 1):
            with Image.open(path) as page:
                page = page.convert("RGB")
                h = round(page.height * thumb_w / page.width)
                page.thumbnail((thumb_w, h), Image.Resampling.LANCZOS)
                thumbs.append((page.copy(), page_no))
        thumb_h = max(image.height for image, _ in thumbs)
        sheet = Image.new("RGB", (margin + columns * (thumb_w + margin), margin + rows * (thumb_h + 56 + margin)), "#D7DEE7")
        draw = ImageDraw.Draw(sheet)
        for i, (page, page_no) in enumerate(thumbs):
            col, row = i % columns, i // columns
            x = margin + col * (thumb_w + margin)
            y = margin + row * (thumb_h + 56 + margin)
            sheet.paste(page, (x, y))
            draw.text((x, y + page.height + 8), f"Page {page_no}", font=font, fill="#17365D")
        number = batch_index // (columns * rows) + 1
        sheet.save(source / f"contact-{number:02d}.jpg", quality=88, optimize=True)
    print(f"Created {(len(pages) + 15) // 16} contact sheets for {len(pages)} pages")


if __name__ == "__main__":
    main()
