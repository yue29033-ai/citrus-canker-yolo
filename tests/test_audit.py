"""Synthetic data tests; never read or modify the real training dataset."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from canker_workbench.audit import audit_dataset, main


class DatasetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for split in ("train", "val", "test"):
            (self.root / "dataset/images" / split).mkdir(parents=True)
            (self.root / "dataset/labels" / split).mkdir(parents=True)
        self.color = 0

    def sample(self, split="train", name="leaf.png", label="0 0.5 0.5 0.2 0.2\n"):
        self.color += 1
        image = self.root / "dataset/images" / split / name
        Image.new("RGB", (20, 20), (self.color, 20, 40)).save(image)
        if label is not None:
            (self.root / "dataset/labels" / split / f"{image.stem}.txt").write_text(label, encoding="utf-8")
        return image

    def leaf_manifest(self, rows):
        path = self.root / "dataset/manifests/leaf_index.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["image_path", "leaf_id", "source"])
            writer.writerows(rows)

    @staticmethod
    def codes(report):
        return {item["code"] for item in report["errors"]}

    def test_valid_dataset_unknown_leaf_ids_and_json_safety(self):
        self.sample()
        self.sample("val", label="")
        report = audit_dataset(self.root)
        self.assertTrue(report["ok"])
        self.assertEqual(report["totals"]["images"], 2)
        self.assertEqual(report["totals"]["boxes"], 1)
        self.assertEqual(report["totals"]["negative_images"], 1)
        self.assertEqual(report["leaf_index"]["status"], "unknown")
        self.assertEqual(report["leaf_index"]["unknown_images"], 2)
        self.assertEqual(report["leaf_index"]["known_leaf_ids"], 0)
        json.dumps(report, allow_nan=False)

    def test_missing_and_orphan_labels_not_negative(self):
        self.sample(label=None)
        (self.root / "dataset/labels/train/orphan.txt").write_text("")
        report = audit_dataset(self.root)
        self.assertEqual(self.codes(report), {"missing_label", "orphan_label"})
        self.assertEqual(report["totals"]["negative_images"], 0)

    def test_duplicate_stems_are_ambiguous(self):
        self.sample(name="same.png", label="")
        self.sample(name="same.jpg", label="")
        report = audit_dataset(self.root)
        self.assertIn("duplicate_stem", self.codes(report))
        self.assertEqual(report["totals"]["images"], 2)
        self.assertEqual(report["totals"]["negative_images"], 0)

    def test_invalid_coordinates_classes_columns_and_finite_values(self):
        cases = {
            "nan": ("0 nan 0.5 0.2 0.2", "label_nonfinite"),
            "inf": ("0 0.5 inf 0.2 0.2", "label_nonfinite"),
            "class": ("1 0.5 0.5 0.2 0.2", "label_class"),
            "floatclass": ("0.0 0.5 0.5 0.2 0.2", "label_number"),
            "negative": ("0 -0.1 0.5 0.2 0.2", "label_normalization"),
            "zero": ("0 0.5 0.5 0 0.2", "label_size"),
            "bounds": ("0 0.95 0.5 0.2 0.2", "label_bounds"),
            "columns": ("0 0.5 0.5 0.2", "label_columns"),
        }
        for name, (label, _) in cases.items():
            self.sample(name=f"{name}.png", label=label)
        report = audit_dataset(self.root)
        self.assertEqual(self.codes(report), {expected for _, expected in cases.values()})
        self.assertEqual(report["totals"]["boxes"], 0)
        self.assertEqual(report["totals"]["invalid_labels"], len(cases))
        self.assertEqual(report["totals"]["negative_images"], 0)

    def test_corner_roundoff_tolerance(self):
        self.sample(label="0 0.1 0.1 0.200001 0.200001\n")
        report = audit_dataset(self.root)
        self.assertTrue(report["ok"])
        self.assertEqual(report["totals"]["boxes"], 1)

    def test_exact_duplicates_within_and_across_splits(self):
        first = self.sample(name="first.png")
        second = self.sample(name="second.png")
        shutil.copyfile(first, second)
        within = audit_dataset(self.root)
        self.assertTrue(within["ok"])
        self.assertFalse(within["duplicate_groups"][0]["cross_split"])
        third = self.sample("test", name="third.png")
        shutil.copyfile(first, third)
        across = audit_dataset(self.root)
        self.assertIn("duplicate_cross_split", self.codes(across))
        self.assertEqual(len(across["duplicate_groups"][0]["images"]), 3)

    def test_corrupt_image_not_counted_as_negative(self):
        image = self.sample(label="")
        image.write_bytes(b"not an image")
        report = audit_dataset(self.root)
        self.assertIn("corrupt_image", self.codes(report))
        self.assertEqual(report["totals"]["corrupt_images_count"], 1)
        self.assertEqual(report["totals"]["negative_images"], 0)

    def test_partial_manifest_and_same_leaf_leakage(self):
        self.sample("train", "a.png")
        self.sample("val", "b.png")
        self.sample("test", "c.png")
        self.leaf_manifest([
            ["dataset/images/train/a.png", "confirmed-leaf-1", "manual"],
            ["dataset/images/val/b.png", "confirmed-leaf-1", "manual"],
        ])
        report = audit_dataset(self.root)
        self.assertIn("leaf_cross_split", self.codes(report))
        self.assertEqual(report["leaf_index"]["status"], "partial")
        self.assertEqual(report["leaf_index"]["unknown_images"], 1)
        self.assertEqual(report["leaf_index"]["known_images"], 2)
        self.assertEqual(report["leaf_index"]["known_leaf_ids"], 1)

    def test_conflicting_manifest_rows_are_excluded(self):
        self.sample("train", "a.png")
        self.leaf_manifest([
            ["dataset/images/train/a.png", "leaf-1", "manual"],
            ["dataset/images/train/a.png", "leaf-2", "manual"],
            ["dataset/images/train/a.png", "leaf-3", "manual"],
        ])
        report = audit_dataset(self.root)
        self.assertIn("leaf_index_duplicate", self.codes(report))
        self.assertEqual(report["leaf_index"]["status"], "invalid")
        self.assertEqual(report["leaf_index"]["known_images"], 0)

    def test_unknown_manifest_paths_and_blank_ids(self):
        self.sample("train", "a.png")
        self.leaf_manifest([
            ["../../outside.jpg", "bad", "manual"],
            ["dataset/images/train/a.png", "", "manual"],
        ])
        report = audit_dataset(self.root)
        self.assertIn("leaf_index_image", self.codes(report))
        self.assertEqual(report["leaf_index"]["unknown_images"], 1)

    def test_missing_directories(self):
        report = audit_dataset(self.root / "absent")
        self.assertIn("missing_directory", self.codes(report))
        self.assertEqual(report["leaf_index"]["coverage"], None)

    def test_cli_exclusive_output(self):
        self.sample()
        destination = self.root / "audit.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--project-root", str(self.root), "--output", str(destination)]), 0)
        original = destination.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--project-root", str(self.root), "--output", str(destination)])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(destination.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
