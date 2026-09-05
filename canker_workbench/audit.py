"""Read-only checks for images, YOLO labels, exact duplicates and known leaves.

The optional ``dataset/manifests/leaf_index.csv`` uses the columns
``image_path,leaf_id,source``. Paths are relative to the project directory,
for example ``dataset/images/val/example.jpg``; absolute paths inside the
same project are also accepted. Missing leaf IDs remain unknown.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image

SPLITS = ("train", "val", "test")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
CORNER_TOLERANCE = 1e-6


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label_summary(path: Path, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    result = {"annotation_rows": 0, "boxes": 0, "empty": False, "valid": False}
    relative = _relative(path, root)
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return result, [_issue("unreadable_label", str(exc), path=relative)]
    result["empty"] = not contents.strip()
    for line_number, line in enumerate(contents.splitlines(), 1):
        if not line.strip():
            continue
        result["annotation_rows"] += 1
        location = {"path": relative, "line": line_number}
        parts = line.split()
        if len(parts) != 5:
            errors.append(_issue("label_columns", "Expected exactly five YOLO columns.", **location))
            continue
        try:
            class_id = int(parts[0])
            x, y, width, height = map(float, parts[1:])
        except ValueError:
            errors.append(_issue("label_number", "Class must be an integer and coordinates numeric.", **location))
            continue
        if class_id != 0:
            errors.append(_issue("label_class", "Only class 0 (canker) is supported.", **location))
            continue
        values = (x, y, width, height)
        if not all(math.isfinite(value) for value in values):
            errors.append(_issue("label_nonfinite", "NaN and infinity are not valid coordinates.", **location))
            continue
        if not all(0 <= value <= 1 for value in values):
            errors.append(_issue("label_normalization", "All coordinates must lie between 0 and 1.", **location))
            continue
        if width <= 0 or height <= 0:
            errors.append(_issue("label_size", "Box width and height must be positive.", **location))
            continue
        if (x - width / 2 < -CORNER_TOLERANCE or y - height / 2 < -CORNER_TOLERANCE
                or x + width / 2 > 1 + CORNER_TOLERANCE or y + height / 2 > 1 + CORNER_TOLERANCE):
            errors.append(_issue("label_bounds", "The box extends beyond the image boundary.", **location))
            continue
        result["boxes"] += 1
    result["valid"] = not errors
    return result, errors


def _leaf_summary(
    root: Path,
    image_splits: dict[str, str],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = root / "dataset/manifests/leaf_index.csv"
    summary: dict[str, Any] = {
        "path": "dataset/manifests/leaf_index.csv",
        "status": "unknown",
        "known_images": 0,
        "unknown_images": len(image_splits),
        "coverage": 0.0 if image_splits else None,
        "known_leaf_ids": 0,
        "cross_split_groups": [],
    }
    if not manifest.is_file():
        warnings.append(_issue("leaf_index_missing", "Physical-leaf IDs are unknown; no complete leaf-leakage check is possible."))
        return summary
    assignments: dict[str, str] = {}
    seen: set[str] = set()
    invalid = False
    try:
        with manifest.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"image_path", "leaf_id", "source"}.issubset(reader.fieldnames or []):
                errors.append(_issue("leaf_index_columns", "The leaf index requires image_path, leaf_id and source columns.", path=summary["path"]))
                summary["status"] = "invalid"
                return summary
            for line, row in enumerate(reader, 2):
                raw_path = (row.get("image_path") or "").strip()
                leaf_id = (row.get("leaf_id") or "").strip()
                source = (row.get("source") or "").strip()
                candidate = Path(raw_path)
                resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
                try:
                    image_path = _relative(resolved, root)
                except ValueError:
                    image_path = ""
                if not raw_path or image_path not in image_splits:
                    invalid = True
                    errors.append(_issue("leaf_index_image", "The manifest entry does not identify a current dataset image.", path=summary["path"], line=line, image_path=raw_path))
                    continue
                if image_path in seen:
                    invalid = True
                    assignments.pop(image_path, None)
                    errors.append(_issue("leaf_index_duplicate", "An image has multiple manifest rows; its leaf assignment is excluded.", path=summary["path"], line=line, image_path=image_path))
                    continue
                seen.add(image_path)
                if leaf_id:
                    assignments[image_path] = leaf_id
                    if not source:
                        warnings.append(_issue("leaf_index_source_missing", "A known leaf ID has no source description.", image_path=image_path))
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(_issue("leaf_index_unreadable", str(exc), path=summary["path"]))
        summary["status"] = "invalid"
        return summary
    groups: dict[str, list[str]] = defaultdict(list)
    for image_path, leaf_id in assignments.items():
        groups[leaf_id].append(image_path)
    for leaf_id, image_paths in sorted(groups.items()):
        splits = sorted({image_splits[path] for path in image_paths})
        if len(splits) > 1:
            group = {"leaf_id": leaf_id, "images": sorted(image_paths), "splits": splits}
            summary["cross_split_groups"].append(group)
            errors.append(_issue("leaf_cross_split", "A known physical leaf appears in multiple splits.", **group))
    known = len(assignments)
    unknown = len(image_splits) - known
    summary.update({
        "status": "invalid" if invalid else ("complete" if unknown == 0 else "partial"),
        "known_images": known,
        "unknown_images": unknown,
        "coverage": known / len(image_splits) if image_splits else None,
        "known_leaf_ids": len(groups),
    })
    if unknown:
        warnings.append(_issue("leaf_index_incomplete", "Some images have no unambiguous leaf ID; same-leaf separation is not fully verified.", unknown_images=unknown))
    return summary


def audit_dataset(project_root: Path) -> dict[str, Any]:
    """Return a JSON-safe audit without changing data, labels, caches or manifests."""
    root = Path(project_root).expanduser().resolve()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": 1,
        "project_root": str(root),
        "splits": {},
        "totals": {},
        "errors": errors,
        "warnings": warnings,
        "duplicate_groups": [],
        "limitations": [
            "SHA256 detects byte-identical files, not recompressed, transformed or different-view images of the same physical leaf.",
            "Only manually supplied leaf IDs are checked; missing IDs must not be interpreted as independent leaves.",
            "Label-format checks cannot confirm biological diagnosis or completeness of lesion annotation.",
            "The current test split is a previously inspected diagnostic set, not an untouched external test.",
        ],
    }
    hashes: dict[str, list[str]] = defaultdict(list)
    image_splits: dict[str, str] = {}
    total_keys = ("images", "labels", "boxes", "annotation_rows", "empty_labels", "negative_images", "positive_images", "invalid_labels", "missing_labels_count", "orphan_labels_count", "corrupt_images_count")
    report["totals"] = dict.fromkeys(total_keys, 0)
    for split in SPLITS:
        image_dir = root / "dataset/images" / split
        label_dir = root / "dataset/labels" / split
        for directory in (image_dir, label_dir):
            if not directory.is_dir():
                errors.append(_issue("missing_directory", "A required dataset directory is absent.", path=_relative(directory, root)))
        images = sorted(path for path in image_dir.glob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
        labels = sorted(path for path in label_dir.glob("*") if path.is_file() and path.suffix.lower() == ".txt")
        image_stems: dict[str, list[Path]] = defaultdict(list)
        label_stems: dict[str, list[Path]] = defaultdict(list)
        for path in images:
            image_stems[path.stem].append(path)
        for path in labels:
            label_stems[path.stem].append(path)
        missing = [_relative(path, root) for path in images if path.stem not in label_stems]
        orphan = [_relative(path, root) for path in labels if path.stem not in image_stems]
        for path in missing:
            errors.append(_issue("missing_label", "An image has no label; it is not counted as a negative image.", path=path))
        for path in orphan:
            errors.append(_issue("orphan_label", "A label has no matching image.", path=path))
        duplicate_stems = []
        for kind, stems in (("image", image_stems), ("label", label_stems)):
            for stem, paths in sorted(stems.items()):
                if len(paths) > 1:
                    item = {"kind": kind, "stem": stem, "paths": [_relative(path, root) for path in paths]}
                    duplicate_stems.append(item)
                    errors.append(_issue("duplicate_stem", "Multiple files share a stem, making image-label pairing ambiguous.", split=split, **item))
        stats: dict[str, Any] = {
            **dict.fromkeys(total_keys, 0),
            "images": len(images), "labels": len(labels),
            "missing_labels": missing, "orphan_labels": orphan,
            "missing_labels_count": len(missing), "orphan_labels_count": len(orphan),
            "duplicate_stems": duplicate_stems, "corrupt_images": [],
        }
        summaries: dict[str, dict[str, Any]] = {}
        for path in labels:
            summary, label_errors = _label_summary(path, root)
            errors.extend(label_errors)
            summaries[path.stem] = summary
            stats["annotation_rows"] += summary["annotation_rows"]
            stats["boxes"] += summary["boxes"]
            stats["empty_labels"] += int(summary["empty"])
            stats["invalid_labels"] += int(not summary["valid"])
        for path in images:
            relative = _relative(path, root)
            image_splits[relative] = split
            try:
                hashes[_sha256(path)].append(relative)
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    image.load()
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
                stats["corrupt_images"].append(relative)
                errors.append(_issue("corrupt_image", str(exc), path=relative))
                continue
            summary = summaries.get(path.stem)
            if summary and summary["valid"] and len(image_stems[path.stem]) == 1 and len(label_stems[path.stem]) == 1:
                stats["negative_images" if summary["empty"] else "positive_images"] += 1
        stats["corrupt_images_count"] = len(stats["corrupt_images"])
        report["splits"][split] = stats
        for key in total_keys:
            report["totals"][key] += stats[key]
    for digest, image_paths in sorted(hashes.items()):
        if len(image_paths) < 2:
            continue
        splits = sorted({image_splits[path] for path in image_paths})
        group = {"sha256": digest, "images": sorted(image_paths), "splits": splits, "cross_split": len(splits) > 1}
        report["duplicate_groups"].append(group)
        if group["cross_split"]:
            errors.append(_issue("duplicate_cross_split", "Byte-identical images appear in different splits.", **group))
        else:
            warnings.append(_issue("duplicate_within_split", "Byte-identical images repeat within one split; they are not independent samples.", **group))
    report["leaf_index"] = _leaf_summary(root, image_splits, errors, warnings)
    report["ok"] = not errors
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Write a new JSON file; existing files are never overwritten.")
    args = parser.parse_args(argv)
    report = audit_dataset(args.project_root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        except OSError as exc:
            parser.exit(2, f"Cannot create audit report: {exc}\n")
    print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
