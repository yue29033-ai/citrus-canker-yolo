"""Read-only contact sheet for choosing a small manual segmentation pilot."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "train": [1, 6, 30, 43, 66, 138, 165, 185, 245, 305, 335, 365, 397, 454],
    "val": [201, 271, 442, 567, 575],
}


def main():
    rows = []
    for split, numbers in CANDIDATES.items():
        images = list((ROOT / "dataset/images" / split).iterdir())
        for number in numbers:
            matches = [p for p in images if p.stem == f"Citrus Canker{number}"]
            if len(matches) == 1:
                rows.append((split, matches[0]))
    size, columns = 260, 4
    sheet = Image.new("RGB", (columns * size, ((len(rows) + columns - 1) // columns) * (size + 25)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (split, path) in enumerate(rows):
        with Image.open(path) as source:
            preview = source.convert("RGB")
        preview.thumbnail((size - 10, size - 10))
        x, y = (index % columns) * size, (index // columns) * (size + 25)
        sheet.paste(preview, (x + (size - preview.width) // 2, y + (size - preview.height) // 2))
        draw.text((x + 8, y + size), f"{split} / {path.stem}", fill="black")
    output = Path(__file__).with_name("candidates.jpg")
    if output.exists():
        raise SystemExit("候选预览已存在，不覆盖。")
    sheet.save(output, quality=88)
    print(output)


if __name__ == "__main__":
    main()
