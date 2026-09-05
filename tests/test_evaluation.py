import csv
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from canker_workbench.evaluation import (
    box_iou, csv_safe, evaluate_dataset, match_detections, read_ground_truth, safe_ratio,
)


class MatchingTests(unittest.TestCase):
    def test_iou_boundaries(self):
        self.assertEqual(box_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1)
        self.assertEqual(box_iou([0, 0, 10, 10], [10, 0, 20, 10]), 0)
        self.assertEqual(box_iou([0, 0, 0, 0], [0, 0, 0, 0]), 0)
        self.assertAlmostEqual(box_iou([0, 0, 10, 10], [0, 0, 5, 10]), 0.5)

    def test_one_ground_truth_cannot_match_two_predictions(self):
        result = match_detections([{"xyxy": [0, 0, 10, 10]}],
                                  [{"xyxy": [0, 0, 10, 10]}, {"xyxy": [0, 0, 9, 9]}])
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 1, 0))
        self.assertEqual(result["matched"][0]["prediction_index"], 0)

    def test_one_prediction_cannot_match_two_truths(self):
        result = match_detections([{"xyxy": [0, 0, 10, 10]}, {"xyxy": [0, 0, 9, 9]}],
                                  [{"xyxy": [0, 0, 10, 10]}])
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 0, 1))

    def test_match_includes_threshold_equality(self):
        result = match_detections([{"xyxy": [0, 0, 10, 10]}], [{"xyxy": [0, 0, 5, 10]}], 0.5)
        self.assertEqual(result["tp"], 1)
        self.assertEqual(match_detections([], [ {"xyxy": [0, 0, 5, 10]}])["fp"], 1)

    def test_safe_empty_denominator_and_formula_names(self):
        self.assertIsNone(safe_ratio(0, 0))
        self.assertEqual(safe_ratio(0, 1), 0)
        self.assertEqual(csv_safe("=SUM(1)"), "'=SUM(1)")
        self.assertEqual(csv_safe(" @malicious"), "' @malicious")
        self.assertEqual(csv_safe("leaf.jpeg"), "leaf.jpeg")


class DatasetEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = []
        self.predictions = {}
        for split in ("val", "test"):
            (self.root / "dataset" / "images" / split).mkdir(parents=True)
            (self.root / "dataset" / "labels" / split).mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def add_image(self, name, label="", split="val"):
        path = self.root / "dataset" / "images" / split / f"{name}.png"
        Image.new("RGB", (100, 100), "white").save(path)
        if label is not None:
            (self.root / "dataset" / "labels" / split / f"{name}.txt").write_text(label)
        return path

    def predictor(self, image_path, case_id, run_dir, settings):
        self.calls.append(image_path.name)
        detections = self.predictions.get(image_path.stem, [])
        return {"id": case_id, "name": image_path.name, "original": f"originals/{case_id}.png",
                "annotated": f"predictions/{case_id}.jpg", "detections": detections,
                "count": len(detections), "inference_ms": 2.0, "width": 100, "height": 100}

    def evaluate(self, **settings):
        return evaluate_dataset(self.root, self.root / "run", settings, self.predictor)

    def test_positive_negative_and_image_level_are_distinct(self):
        self.add_image("positive", "0 0.5 0.5 0.2 0.2")
        self.add_image("negative")
        self.predictions = {"positive": [{"xyxy": [0, 0, 10, 10], "confidence": 0.9}],
                            "negative": [{"xyxy": [20, 20, 30, 30], "confidence": 0.7}]}
        result = self.evaluate()
        self.assertEqual(result["metrics"]["lesion"]["tp"], 0)
        self.assertEqual(result["metrics"]["lesion"]["fp"], 2)
        self.assertEqual(result["metrics"]["lesion"]["fn"], 1)
        self.assertEqual(result["metrics"]["image"]["sensitivity"], 1)
        self.assertEqual(result["metrics"]["image"]["false_positive_rate"], 1)
        cases = {c["name"]: c for c in result["cases"]}
        self.assertEqual(cases["positive.png"]["error_type"], "both")
        self.assertEqual(cases["negative.png"]["error_type"], "false_positive")
        self.assertEqual(len(result["dataset_sha256"]), 64)
        self.assertTrue((self.root / "run" / cases["positive.png"]["comparison"]).is_file())
        self.assertNotIn("map", result["metrics"])
        json.dumps(result, allow_nan=False)

    def test_empty_negative_denominators(self):
        self.add_image("negative")
        result = self.evaluate()
        self.assertIsNone(result["metrics"]["lesion"]["precision"])
        self.assertIsNone(result["metrics"]["lesion"]["recall"])
        self.assertIsNone(result["metrics"]["lesion"]["f1"])
        self.assertIsNone(result["metrics"]["image"]["sensitivity"])
        self.assertEqual(result["metrics"]["image"]["specificity"], 1)

    def test_missing_label_fails_before_any_inference(self):
        self.add_image("a_valid")
        self.add_image("z_missing", None)
        with self.assertRaisesRegex(ValueError, "缺少标签"):
            self.evaluate()
        self.assertEqual(self.calls, [])

    def test_invalid_label_variants_fail_before_inference(self):
        path = self.add_image("bad")
        label = self.root / "dataset" / "labels" / "val" / "bad.txt"
        for text in ("1 0.5 0.5 0.2 0.2", "0 nan 0.5 0.2 0.2", "0 .9 .5 .4 .2",
                     "0 .5 .5 0 .2", "0 .5 .5 .2", "0 q .5 .2 .2"):
            with self.subTest(label=text):
                label.write_text(text)
                with self.assertRaises(ValueError):
                    self.evaluate()
        self.assertEqual(self.calls, [])

    def test_exact_border_and_bom_labels(self):
        path = self.add_image("border", "\ufeff0 0.5 0.5 1 1\n")
        label = self.root / "dataset" / "labels" / "val" / "border.txt"
        self.assertEqual(read_ground_truth(label, 100, 100)[0]["xyxy"], [0, 0, 100, 100])

    def test_filter_predictions_and_keep_test_diagnostic_warning(self):
        self.add_image("positive", "0 .5 .5 .2 .2", split="test")
        self.predictions = {"positive": [{"xyxy": [40, 40, 60, 60], "confidence": 0.54}]}
        result = self.evaluate(split="test", conf=0.55)
        self.assertIn("阶段性诊断", result["warning"])
        self.assertEqual(result["cases"][0]["count"], 0)
        self.assertEqual(result["cases"][0]["error_type"], "false_negative")
        self.assertIsNone(result["metrics"]["image"]["false_positive_rate"])

    def test_data_hash_changes_with_label_contents(self):
        self.add_image("a", "")
        first = self.evaluate()["dataset_sha256"]
        (self.root / "dataset" / "labels" / "val" / "a.txt").write_text("0 .5 .5 .2 .2")
        second = evaluate_dataset(self.root, self.root / "another_run", {}, self.predictor)["dataset_sha256"]
        self.assertNotEqual(first, second)

    def test_overwrite_is_refused(self):
        self.add_image("a")
        self.evaluate()
        with self.assertRaisesRegex(ValueError, "已有评估结果"):
            self.evaluate()

    def test_formula_filename_is_escaped_in_csv(self):
        self.add_image("=1+1")
        self.evaluate()
        with (self.root / "run" / "per_image.csv").open(encoding="utf-8-sig") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["name"], "'=1+1.png")

    def test_duplicate_stem_rejected(self):
        self.add_image("a")
        Image.new("RGB", (100, 100)).save(self.root / "dataset" / "images" / "val" / "a.jpg")
        with self.assertRaisesRegex(ValueError, "同名"):
            self.evaluate()

    def test_orphan_label_rejected(self):
        self.add_image("a")
        (self.root / "dataset" / "labels" / "val" / "orphan.txt").write_text("")
        with self.assertRaisesRegex(ValueError, "没有图片的标签"):
            self.evaluate()
        self.assertEqual(self.calls, [])

    def test_source_mutation_during_evaluation_rejected(self):
        self.add_image("a")
        def mutating_predictor(*args):
            case = self.predictor(*args)
            (self.root / "dataset" / "labels" / "val" / "a.txt").write_text("0 .5 .5 .2 .2")
            return case
        with self.assertRaisesRegex(RuntimeError, "数据集发生变化"):
            evaluate_dataset(self.root, self.root / "run", {}, mutating_predictor)

    def test_other_prediction_class_rejected(self):
        self.add_image("a")
        self.predictions = {"a": [{"xyxy": [0, 0, 10, 10], "confidence": .9, "class_id": 1}]}
        with self.assertRaisesRegex(ValueError, "其他类别"):
            self.evaluate()

    def test_optional_map_uses_only_snapshot_paths(self):
        self.add_image("a", "0 .5 .5 .2 .2")
        weights = self.root / "weights.pt"
        weights.write_bytes(b"test-double")
        source_cache = self.root / "dataset" / "labels" / "val.cache"
        source_cache.write_bytes(b"untouched")
        calls = []
        class FakeYOLO:
            def __init__(self, path):
                self.path = path
            def val(self, **kwargs):
                calls.append(kwargs)
                data = json.loads(Path(kwargs["data"]).read_text())
                snapshot = Path(data["path"])
                self.assert_snapshot = snapshot
                (snapshot / "labels" / "val.cache").write_bytes(b"new-cache")
                return types.SimpleNamespace(box=types.SimpleNamespace(map50=0.8, map=0.6),
                                             save_dir=Path(kwargs["project"]) / kwargs["name"])
        with patch.dict("sys.modules", {"ultralytics": types.SimpleNamespace(YOLO=FakeYOLO)}):
            result = self.evaluate(include_map=True, weights=str(weights))
        self.assertEqual(result["metrics"]["map"]["map50"], 0.8)
        self.assertEqual(source_cache.read_bytes(), b"untouched")
        self.assertTrue(Path(calls[0]["data"]).is_relative_to((self.root / "run").resolve()))
        self.assertEqual(calls[0]["conf"], 0.001)
        self.assertEqual((self.root / "run" / "map_snapshot" / "labels" / "val" / "a.txt").read_text(),
                         "0 .5 .5 .2 .2")


if __name__ == "__main__":
    unittest.main()
