import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from canker_workbench.core import Workbench, inside
from canker_workbench.server import decode_upload


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        (self.root / "weights.pt").write_bytes(b"fake weights, never loaded")
        (self.root / "configs/workbench.json").write_text(json.dumps({"weights": "weights.pt", "imgsz": 960, "conf": .55, "iou": .7, "match_iou": .5, "device": "cpu", "max_det": 300, "split": "val", "include_map": False}))
        self.wb = Workbench(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_paths_cannot_escape(self):
        for value in ("../private", "/etc/passwd", "."):
            with self.assertRaises(ValueError):
                inside(self.root, value)
        with self.assertRaises(ValueError):
            self.wb.run_dir("../../dataset")

    def test_settings_reject_invalid_and_missing_model(self):
        for setting in ({"imgsz": 641}, {"conf": float("nan")}, {"conf": True}, {"split": "train"}, {"include_map": "yes"}, {"typo": 1}):
            with self.assertRaises(ValueError):
                self.wb.settings(setting)
        with self.assertRaises(ValueError):
            self.wb.settings({"weights": "missing.pt"})

    def test_prediction_review_and_export_without_touching_labels(self):
        image_path = self.root / "test.png"
        Image.new("RGB", (20, 20), "green").save(image_path)

        def fake_predict(path, case_id, run_dir, settings):
            return {"id": case_id, "name": path.name, "detections": [], "count": 0, "inference_ms": 1.0, "source_sha256": "test"}

        self.wb.predict_image = fake_predict
        run = self.wb.predict([image_path], names=["=danger.png"])
        self.assertEqual(run["status"], "completed")
        self.assertTrue((self.wb.run_dir(run["id"]) / "results.zip").exists())
        data = (self.wb.run_dir(run["id"]) / "per_image.csv").read_text(encoding="utf-8-sig")
        self.assertIn("'=danger.png", data)
        first = self.wb.review(run["id"], "0001", "uncertain", note="=1+1")
        second = self.wb.review(run["id"], "0001", "correct", reason="checked")
        self.assertFalse(first["label_modified"])
        self.assertEqual(self.wb.get_run(run["id"])["cases"][0]["review"]["id"], second["id"])
        rows = list(csv.reader(io.StringIO(self.wb.review_csv().decode("utf-8-sig"))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][4], "'=1+1")
        self.assertFalse((self.root / "dataset").exists())
        with self.assertRaises(ValueError):
            self.wb.review(run["id"], "9999", "correct")

    def test_unique_runs(self):
        one, _ = self.wb.create_run("prediction", self.wb.settings())
        two, _ = self.wb.create_run("prediction", self.wb.settings())
        self.assertNotEqual(one, two)

    def test_upload_validation(self):
        import base64
        content = io.BytesIO()
        Image.new("RGB", (20, 20), "green").save(content, format="PNG")
        name, raw, normalized = decode_upload({"name": "../../leaf.png", "data": base64.b64encode(content.getvalue()).decode()})
        self.assertEqual(name, "leaf.png")
        self.assertEqual(normalized.size, (20, 20))
        with self.assertRaises(ValueError):
            decode_upload({"name": "bad.svg", "data": base64.b64encode(b"<svg></svg>").decode()})
        with self.assertRaises(ValueError):
            decode_upload({"name": "bad.png", "data": "@@@"})


if __name__ == "__main__":
    unittest.main()
