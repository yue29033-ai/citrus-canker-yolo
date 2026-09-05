"""Loopback API regressions using disposable data and mocked inference only."""

from __future__ import annotations

import base64
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from PIL import Image

from canker_workbench.core import Workbench, inside, write_json
from canker_workbench.server import LocalServer, decode_upload


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "configs").mkdir()
        (self.root / "web").mkdir()
        (self.root / "web/simple.html").write_text("<!doctype html><title>Fixture</title>")
        (self.root / "local.pt").write_bytes(b"synthetic-not-a-model")
        defaults = {
            "weights": "local.pt", "imgsz": 960, "conf": .55, "iou": .7,
            "match_iou": .5, "device": "cpu", "max_det": 300,
            "split": "val", "include_map": False,
        }
        (self.root / "configs/workbench.json").write_text(json.dumps(defaults))
        for split in ("train", "val", "test"):
            (self.root / "dataset/images" / split).mkdir(parents=True)
            (self.root / "dataset/labels" / split).mkdir(parents=True)
        self.protected_label = self.root / "dataset/labels/train/fixture.txt"
        self.protected_label.write_text("0 0.5 0.5 0.2 0.2\n")
        self.protected_contents = self.protected_label.read_bytes()
        self.workbench = Workbench(self.root)
        self.server = LocalServer(("127.0.0.1", 0), self.workbench)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.executor.shutdown(wait=True)
        self.thread.join(timeout=2)
        self.assertEqual(self.protected_label.read_bytes(), self.protected_contents)
        self.temporary.cleanup()

    def request(self, method, path, payload=None, headers=None, authorized=True):
        supplied = dict(headers or {})
        body = None if payload is None else json.dumps(payload).encode()
        if body is not None:
            supplied.setdefault("Content-Type", "application/json")
        if method == "POST" and authorized:
            supplied.setdefault("X-CSRF-Token", self.server.csrf_token)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=supplied)
            response = connection.getresponse()
            content = response.read()
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def wait_job(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, _, content = self.request("GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            result = json.loads(content)
            if result["status"] not in {"queued", "running"}:
                return result
            threading.Event().wait(.01)
        self.fail("Mock job did not finish")

    def fixture_run(self):
        directory, record = self.workbench.create_run("prediction", self.workbench.settings())
        record.update({"status": "completed", "cases": [{"id": "0001", "name": "fixture.png"}], "case_count": 1})
        write_json(directory / "run.json", record)
        return record["id"], directory

    @staticmethod
    def upload(name="leaf.png", image_format="PNG", size=(8, 8)):
        output = io.BytesIO()
        Image.new("RGB", size, (25, 80, 20)).save(output, format=image_format)
        return {"name": name, "data": base64.b64encode(output.getvalue()).decode()}

    def test_status_and_security_headers(self):
        status, headers, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrf_token"], self.server.csrf_token)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_polygon_routes_save_and_reload_draft(self):
        from test_polygons import fixture, LEAF
        row = fixture(self.root)[0]
        status, _, body = self.request("GET", "/api/annotations")
        self.assertEqual(status,200)
        self.assertEqual(len(json.loads(body)["samples"]),2)
        self.assertEqual(self.request("GET", "/api/annotation/image/" + row["id"])[0],200)
        payload = {"id":row["id"],"shapes":[LEAF],"complete":False,"leaf_id":"draft"}
        self.assertEqual(self.request("POST", "/api/annotation",payload,authorized=False)[0],403)
        self.assertEqual(self.request("POST", "/api/annotation",payload)[0],200)
        status, _, body = self.request("GET", "/api/annotation/" + row["id"])
        self.assertEqual(status,200)
        self.assertFalse(json.loads(body)["annotation"]["flags"]["complete"])

    def test_foreign_host_and_origin_rejected(self):
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/status", headers=headers)[0], 403)
                self.assertEqual(self.request("POST", "/api/evaluate", {}, headers=headers)[0], 403)

    def test_missing_and_invalid_csrf_rejected(self):
        self.assertEqual(self.request("POST", "/api/evaluate", {}, authorized=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/evaluate", {}, headers={"X-CSRF-Token": "wrong"})[0], 403)

    def test_non_ascii_csrf_rejected_cleanly(self):
        self.assertEqual(self.request("POST", "/api/evaluate", {}, headers={"X-CSRF-Token": "caf\u00e9"})[0], 403)

    def test_malformed_settings_and_json_object(self):
        for settings in ({"weights": "outside.pt"}, {"conf": float("nan")}, {"imgsz": 999}, [], {"split": "train"}):
            with self.subTest(settings=settings):
                self.assertEqual(self.request("POST", "/api/evaluate", {"settings": settings})[0], 400)
        self.assertEqual(self.request("POST", "/api/evaluate", [])[0], 400)
        self.assertEqual(self.request("POST", "/api/evaluate", {}, headers={"Content-Type": "text/plain"})[0], 400)

    def test_traversal_cannot_read_outside_run(self):
        run_id, run_dir = self.fixture_run()
        (run_dir / "sample.txt").write_text("public run artifact")
        status, _, body = self.request("GET", f"/files/{run_id}/sample.txt")
        self.assertEqual((status, body), (200, b"public run artifact"))
        for suffix in ("../run.json", "%2E%2E/%2E%2E/reviews.jsonl", "/etc/passwd"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.request("GET", f"/files/{run_id}/{suffix}")[0], 400)
        self.assertEqual(self.request("GET", "/api/runs/../../dataset")[0], 400)
        with self.assertRaises(ValueError):
            inside(run_dir, "../../../dataset/labels/train/fixture.txt")

    def test_image_upload_is_normalized_and_safely_named(self):
        calls = []

        def predict(paths, overrides, progress, names):
            calls.append((paths, overrides, names))
            self.assertEqual(names, ["photo.png"])
            self.assertTrue(all(path.is_relative_to(self.workbench.workspace / "uploads") for path in paths))
            self.assertTrue(all(path.is_file() for path in paths))
            return {"id": "mock-prediction"}

        with mock.patch.object(self.workbench, "predict", side_effect=predict):
            status, _, body = self.request("POST", "/api/predict", {"files": [self.upload("../../photo.png")]})
            self.assertEqual(status, 202)
            job = self.wait_job(json.loads(body)["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.root / "photo.png").exists())

    def test_invalid_uploads_do_not_create_jobs(self):
        for files in ([], [self.upload()] * 13, [{"name": "leaf.png", "data": "!invalid base64"}], [self.upload(image_format="GIF")]):
            with self.subTest(files_count=len(files)):
                self.assertEqual(self.request("POST", "/api/predict", {"files": files})[0], 400)
        self.assertFalse(self.server.jobs)
        self.assertFalse((self.workbench.workspace / "uploads").exists())

    def test_per_image_pixel_limit(self):
        with mock.patch("canker_workbench.server.MAX_PIXELS", 20):
            with self.assertRaises(ValueError):
                decode_upload(self.upload(size=(8, 8)))

    def test_batch_pixel_budget_rejected_before_saving(self):
        with mock.patch("canker_workbench.server.MAX_BATCH_PIXELS", 100):
            status, _, _ = self.request("POST", "/api/predict", {"files": [self.upload(), self.upload()]})
        self.assertEqual(status, 400)
        self.assertFalse(self.server.jobs)
        self.assertFalse((self.workbench.workspace / "uploads").exists())

    def test_busy_upload_rejected_before_image_decode(self):
        release = threading.Event()
        job_id = self.server.submit(lambda progress: (release.wait(2), {"id": "busy-fixture"})[1])
        try:
            with mock.patch("canker_workbench.server.decode_upload") as decoder:
                status, _, _ = self.request("POST", "/api/predict", {"files": [self.upload()]})
            self.assertEqual(status, 400)
            decoder.assert_not_called()
        finally:
            release.set()
        self.assertEqual(self.wait_job(job_id)["status"], "completed")

    def test_evaluation_routes_to_shared_workbench(self):
        with mock.patch.object(self.workbench, "evaluate", return_value={"id": "mock-evaluation"}) as evaluate:
            status, _, body = self.request("POST", "/api/evaluate", {"settings": {"split": "val", "include_map": False}})
            self.assertEqual(status, 202)
            job = self.wait_job(json.loads(body)["job_id"])
        self.assertEqual(job["run_id"], "mock-evaluation")
        self.assertEqual(evaluate.call_args.args[0], {"split": "val", "include_map": False})

    def test_one_job_at_a_time_and_failure_recorded(self):
        release = threading.Event()
        started = threading.Event()

        def work(progress):
            started.set()
            release.wait(2)
            raise ValueError("controlled failure")

        job_id = self.server.submit(work)
        try:
            self.assertTrue(started.wait(1))
            with self.assertRaises(ValueError):
                self.server.submit(lambda progress: {"id": "unexpected"})
        finally:
            release.set()
        job = self.wait_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("controlled failure", job["error"])

    def test_review_append_only_and_latest_view(self):
        run_id, _ = self.fixture_run()
        first = {"run_id": run_id, "case_id": "0001", "status": "uncertain", "reason": "needs inspection"}
        second = {**first, "status": "false_positive", "note": "=unsafe spreadsheet formula"}
        self.assertEqual(self.request("POST", "/api/review", first)[0], 200)
        self.assertEqual(self.request("POST", "/api/review", second)[0], 200)
        rows = [json.loads(line) for line in (self.workbench.workspace / "reviews.jsonl").read_text().splitlines()]
        self.assertEqual([row["status"] for row in rows], ["uncertain", "false_positive"])
        self.assertTrue(all(row["label_modified"] is False for row in rows))
        status, _, body = self.request("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["cases"][0]["review"]["status"], "false_positive")
        _, _, exported = self.request("GET", "/api/reviews.csv")
        self.assertIn("'=unsafe spreadsheet formula", exported.decode("utf-8-sig"))

    def test_invalid_review_does_not_append(self):
        run_id, _ = self.fixture_run()
        for values in ({"case_id": "missing", "status": "correct"}, {"case_id": "0001", "status": "healthy"}, {"case_id": "0001", "status": "correct", "note": "x" * 2001}):
            self.assertEqual(self.request("POST", "/api/review", {"run_id": run_id, **values})[0], 400)
        self.assertFalse((self.workbench.workspace / "reviews.jsonl").exists())

    def test_parallel_reviews_are_complete_json_lines(self):
        run_id, _ = self.fixture_run()

        def append(index):
            return self.workbench.review(run_id, "0001", "uncertain", note=f"review {index}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            saved = list(executor.map(append, range(16)))
        rows = [json.loads(line) for line in (self.workbench.workspace / "reviews.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 16)
        self.assertEqual({row["id"] for row in rows}, {row["id"] for row in saved})


if __name__ == "__main__":
    unittest.main()
