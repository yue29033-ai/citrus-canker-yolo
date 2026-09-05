"""Copy an explicit 10-image starter pack; do not invent segmentation labels."""
import csv
import hashlib
import shutil
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PILOT = Path(__file__).resolve().parent
SELECTION = {
    "train": ["Citrus Canker1.jpeg", "Citrus Canker2.jpeg", "Citrus Canker43.jpeg", "Citrus Canker335.jpeg",
              "Healthy Leaf1.jpeg", "Healthy Leaf189.jpeg"],
    "val": ["Citrus Canker271.jpeg", "Citrus Canker442.jpeg", "Yellow Leaves124.jpeg", "Citrus Greening57.jpeg"],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    existing = (PILOT / "selection.csv").exists()
    rows = []
    for split, names in SELECTION.items():
        for name in names:
            source = ROOT / "dataset/images" / split / name
            if not source.is_file():
                raise SystemExit(f"缺少来源图片：{source}")
            rows.append({"source_path": source.relative_to(ROOT).as_posix(), "original_split": split,
                         "filename": name, "sha256": digest(source), "leaf_id": "", "annotation_status": "pending",
                         "source_label_status": "positive_in_existing_dataset" if name.startswith("Citrus Canker") else "negative_in_existing_dataset"})
    if existing:
        with (PILOT / "selection.csv").open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
    for row in rows:
        source = (ROOT / row["source_path"]).resolve()
        if not source.is_relative_to((ROOT / "dataset/images").resolve()) or digest(source) != row["sha256"]:
            raise SystemExit(f"来源与清单不一致：{source}")
        if row["original_split"] not in {"train", "val"} or Path(row["filename"]).name != row["filename"]:
            raise SystemExit("清单路径无效")
        folder = PILOT / "images" / row["original_split"]
        folder.mkdir(parents=True, exist_ok=True)
        (PILOT / "annotations" / row["original_split"]).mkdir(parents=True, exist_ok=True)
        target = folder / row["filename"]
        if target.exists():
            if digest(target) != row["sha256"]:
                raise SystemExit(f"目标文件不同，拒绝覆盖：{target}")
        else:
            shutil.copy2(ROOT / row["source_path"], target)
        if digest(target) != row["sha256"]:
            raise SystemExit("副本校验失败。")
    if not existing:
        with (PILOT / "selection.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    size, columns = 280, 4
    sheet = Image.new("RGB", (size * columns, (size + 30) * 3), "white")
    draw = ImageDraw.Draw(sheet)
    for index, row in enumerate(rows):
        with Image.open(ROOT / row["source_path"]) as source:
            image = source.convert("RGB")
        image.thumbnail((size - 10, size - 10))
        x, y = index % columns * size, index // columns * (size + 30)
        sheet.paste(image, (x + (size - image.width) // 2, y + (size - image.height) // 2))
        draw.text((x + 6, y + size), f"{row['original_split']} / {row['filename']}", fill="black")
    sheet.save(PILOT / "selection_preview.jpg", quality=88)
    print(f"Prepared {len(rows)} exact image copies, no segmentation labels created.")


if __name__ == "__main__":
    main()
