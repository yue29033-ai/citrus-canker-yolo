from pathlib import Path


ROOT = Path(__file__).resolve().parent
IMAGE_ROOT = ROOT / "dataset" / "images"
LABEL_ROOT = ROOT / "dataset" / "labels"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXPECTED_COUNTS = {"train": 281, "val": 65, "test": 55}


def validate_split(split: str) -> tuple[int, int, int, int, list[str]]:
    image_dir = IMAGE_ROOT / split
    label_dir = LABEL_ROOT / split
    errors: list[str] = []

    images = {
        path.stem: path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }
    labels = {path.stem: path for path in label_dir.glob("*.txt") if path.is_file()}

    missing_labels = sorted(images.keys() - labels.keys())
    orphan_labels = sorted(labels.keys() - images.keys())
    if missing_labels:
        errors.append(f"{split}: missing labels: {missing_labels}")
    if orphan_labels:
        errors.append(f"{split}: labels without images: {orphan_labels}")
    if len(images) != EXPECTED_COUNTS[split]:
        errors.append(
            f"{split}: expected {EXPECTED_COUNTS[split]} images, found {len(images)}"
        )

    box_count = 0
    empty_count = 0
    for label_path in labels.values():
        lines = [line.strip() for line in label_path.read_text().splitlines() if line.strip()]
        if not lines:
            empty_count += 1
        for line_number, line in enumerate(lines, 1):
            parts = line.split()
            if len(parts) != 5:
                errors.append(f"{label_path}:{line_number}: expected 5 columns")
                continue
            try:
                class_id = int(parts[0])
                x_center, y_center, width, height = map(float, parts[1:])
            except ValueError:
                errors.append(f"{label_path}:{line_number}: non-numeric value")
                continue

            if class_id != 0:
                errors.append(f"{label_path}:{line_number}: unexpected class {class_id}")
            if not all(
                0.0 <= value <= 1.0
                for value in (x_center, y_center, width, height)
            ):
                errors.append(f"{label_path}:{line_number}: coordinate outside 0-1")
            if width <= 0.0 or height <= 0.0:
                errors.append(f"{label_path}:{line_number}: non-positive box size")
            box_count += 1

    return len(images), len(labels), box_count, empty_count, errors


def main() -> int:
    all_errors: list[str] = []
    total_images = 0
    total_boxes = 0

    for split in ("train", "val", "test"):
        images, labels, boxes, empty, errors = validate_split(split)
        total_images += images
        total_boxes += boxes
        all_errors.extend(errors)
        print(
            f"{split}: images={images}, labels={labels}, "
            f"boxes={boxes}, empty_labels={empty}"
        )

    print(f"total_images={total_images}")
    print(f"total_boxes={total_boxes}")
    print(f"errors={len(all_errors)}")
    for error in all_errors:
        print(f"ERROR: {error}")

    return 1 if all_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
