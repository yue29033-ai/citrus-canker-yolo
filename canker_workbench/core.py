"""Shared inference and append-only review storage. Never writes training data."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import math
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
REVIEW_STATUSES = {"correct", "false_positive", "false_negative", "uncertain"}
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{10}$")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    """Atomic metadata update within a uniquely named run directory."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def csv_safe(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def inside(base, relative):
    base = Path(base).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError("文件路径无效")
    return path


def draw_boxes(image, detections):
    output = image.copy()
    draw = ImageDraw.Draw(output)
    width = max(2, round(min(output.size) / 220))
    for index, detection in enumerate(detections, 1):
        box = detection["xyxy"]
        draw.rectangle(box, outline="#ef932d", width=width)
        label = f"{index} canker {detection['confidence']:.2f}"
        x, y = box[0], max(0, box[1] - 15)
        text_box = draw.textbbox((x, y), label)
        draw.rectangle(text_box, fill="#18382a")
        draw.text((x, y), label, fill="white")
    return output


class Workbench:
    def __init__(self, project_root=ROOT, workspace=None):
        self.root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve() if workspace else self.root / "workspace"
        self.defaults = json.loads((self.root / "configs/workbench.json").read_text(encoding="utf-8"))
        self.lock = threading.RLock()
        self.model = None
        self.model_path = None

    def settings(self, overrides=None):
        config = dict(self.defaults)
        unknown = set(overrides or {}) - set(config)
        if unknown:
            raise ValueError("未知设置：" + ", ".join(sorted(unknown)))
        config.update(overrides or {})
        if type(config["imgsz"]) is not int or config["imgsz"] not in (640, 960):
            raise ValueError("图片尺寸只支持 640 或 960")
        for key in ("conf", "iou", "match_iou"):
            val = config[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 < val < 1:
                raise ValueError(f"{key} 必须是 0 与 1 之间的数")
        if config["split"] not in ("val", "test"):
            raise ValueError("第一阶段只评估 val 或当前诊断 test")
        if type(config["include_map"]) is not bool:
            raise ValueError("include_map 必须是布尔值")
        if config["device"] != "cpu" or config["max_det"] != 300:
            raise ValueError("当前基线固定使用 CPU 和 max_det=300")
        weights = (self.root / config["weights"]).resolve()
        if not weights.is_file():
            raise ValueError("找不到本地模型权重，请查看使用说明；程序不会自动下载模型。")
        config["weights"] = str(weights)
        return config

    def runtime(self):
        import platform
        versions = {"python": platform.python_version()}
        for name in ("ultralytics", "torch", "numpy", "Pillow"):
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = "unavailable"
        return versions

    def status(self):
        weights = self.root / self.defaults["weights"]
        return {
            "model_ready": weights.is_file(), "model_name": "YOLO11n · train-5",
            "defaults": dict(self.defaults), "runtime": self.runtime(),
            "counts": {split: len([p for p in (self.root / "dataset/images" / split).glob("*") if p.suffix.lower() in IMAGE_SUFFIXES]) for split in ("train", "val", "test")},
            "warnings": ["未检出不等于健康；模型置信度不是患病概率。", "0.55 是现有诊断集演示阈值，尚未经独立外部测试验证。", "复核记录不会自动修改标签或进入训练。"],
        }

    def run_dir(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError("运行编号无效")
        return inside(self.workspace / "runs", run_id)

    def create_run(self, kind, settings):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:10]
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        record = {
            "id": run_id, "kind": kind, "created_at": utc_now(), "status": "running",
            "settings": settings, "runtime": self.runtime(), "weights_sha256": sha256(settings["weights"]),
            "cases": [], "case_count": 0, "artifacts": {},
        }
        write_json(run_dir / "run.json", record)
        return run_dir, record

    def _load_model(self, settings):
        if self.model is None or self.model_path != settings["weights"]:
            from ultralytics import YOLO
            self.model = YOLO(settings["weights"])
            self.model_path = settings["weights"]
        return self.model

    def predict_image(self, image_path, case_id, run_dir, settings):
        """Use unrotated image pixels: dataset YOLO coordinates stay unchanged."""
        import numpy as np
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        width, height = image.size
        model = self._load_model(settings)
        started = time.perf_counter()
        result = model.predict(
            source=np.ascontiguousarray(np.asarray(image)[:, :, ::-1]),
            imgsz=settings["imgsz"], conf=settings["conf"], iou=settings["iou"],
            max_det=settings["max_det"], device=settings["device"],
            verbose=False, save=False, augment=False,
        )[0]
        elapsed = (time.perf_counter() - started) * 1000
        detections = []
        for box in result.boxes:
            coords = [round(float(v), 3) for v in box.xyxy[0].tolist()]
            detections.append({"xyxy": coords, "confidence": float(box.conf[0]), "class_id": int(box.cls[0])})
        for folder in ("images", "annotated", "crops"):
            (run_dir / folder).mkdir(exist_ok=True)
        original = f"images/{case_id}.png"
        annotated = f"annotated/{case_id}.jpg"
        image.save(run_dir / original)
        draw_boxes(image, detections).save(run_dir / annotated, quality=92)
        for index, detection in enumerate(detections, 1):
            x1, y1, x2, y2 = detection["xyxy"]
            pad = max(8, int(max(x2 - x1, y2 - y1) * .15))
            bounds = (max(0, int(x1) - pad), max(0, int(y1) - pad), min(width, math.ceil(x2) + pad), min(height, math.ceil(y2) + pad))
            crop_path = f"crops/{case_id}-{index}.jpg"
            image.crop(bounds).save(run_dir / crop_path, quality=94)
            detection.update({"id": index, "crop": crop_path})
        return {
            "id": case_id, "name": Path(image_path).name, "original": original, "annotated": annotated,
            "detections": detections, "count": len(detections), "width": width, "height": height,
            "inference_ms": round(float(result.speed.get("inference", elapsed)), 3),
            "pipeline_ms": round(elapsed, 3), "source_sha256": sha256(image_path),
        }

    def _finish(self, run_dir, record):
        record["status"] = "completed"
        record["completed_at"] = utc_now()
        record["case_count"] = len(record["cases"])
        record["artifacts"]["zip"] = "results.zip"
        write_json(run_dir / "run.json", record)
        # Snapshot export, excludes large validation snapshots and model internals.
        selected = {"run.json", "per_image.csv", "metrics.json", "REPORT.md"}
        with zipfile.ZipFile(run_dir / "results.zip", "x", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(run_dir.rglob("*")):
                relative = path.relative_to(run_dir)
                if path.is_file() and (relative.parts[0] in {"images", "annotated", "crops", "comparisons", "official_map"} or str(relative) in selected):
                    archive.write(path, str(relative))
        return record

    def predict(self, files, overrides=None, progress=None, names=None):
        with self.lock:
            settings = self.settings(overrides)
            if not files:
                raise ValueError("请先选择图片")
            run_dir, record = self.create_run("prediction", settings)
            try:
                for index, path in enumerate(files, 1):
                    if progress:
                        progress(index - 1, len(files), f"正在检测第 {index} / {len(files)} 张")
                    case = self.predict_image(Path(path), f"{index:04d}", run_dir, settings)
                    if names:
                        case["name"] = names[index - 1]
                    record["cases"].append(case)
                with (run_dir / "per_image.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["image", "detected_lesions", "max_confidence", "inference_ms", "result", "source_sha256"])
                    for case in record["cases"]:
                        writer.writerow([csv_safe(case["name"]), case["count"], max((d["confidence"] for d in case["detections"]), default=""), case["inference_ms"], "疑似溃疡病斑" if case["count"] else "未检出溃疡病斑（不等于健康）", case["source_sha256"]])
                (run_dir / "REPORT.md").write_text(
                    f"# 检测记录\n\n运行：{record['id']}\n\n共 {len(files)} 张图片。\n\n"
                    "本记录没有人工真值，不能计算准确率或 mAP。模型输出仅供辅助观察。\n"
                    "未检出不等于叶片健康，置信度不是患病概率。复核记录独立保存，不自动改标签。\n",
                    encoding="utf-8",
                )
                record["artifacts"] = {"csv": "per_image.csv", "report": "REPORT.md"}
                if progress:
                    progress(len(files), len(files), "检测完成")
                return self._finish(run_dir, record)
            except Exception as exc:
                record.update({"status": "failed", "error": str(exc)})
                write_json(run_dir / "run.json", record)
                raise

    def evaluate(self, overrides=None, progress=None):
        from .evaluation import evaluate_dataset
        with self.lock:
            settings = self.settings(overrides)
            run_dir, record = self.create_run("evaluation", settings)
            try:
                result = evaluate_dataset(self.root, run_dir, settings, self.predict_image, progress)
                record.update(result)
                return self._finish(run_dir, record)
            except Exception as exc:
                record.update({"status": "failed", "error": str(exc)})
                write_json(run_dir / "run.json", record)
                raise

    def _reviews(self):
        path = self.workspace / "reviews.jsonl"
        if not path.exists():
            return []
        with self.lock, path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def get_run(self, run_id):
        record = json.loads((self.run_dir(run_id) / "run.json").read_text(encoding="utf-8"))
        latest = {entry["case_id"]: entry for entry in self._reviews() if entry["run_id"] == run_id}
        for case in record.get("cases", []):
            case["review"] = latest.get(case["id"])
        return record

    def list_runs(self):
        records = []
        for path in sorted((self.workspace / "runs").glob("*/run.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append({k: v for k, v in record.items() if k not in ("cases",)})
            except (OSError, ValueError):
                continue
        return records

    def review(self, run_id, case_id, status, reason="", note=""):
        if status not in REVIEW_STATUSES:
            raise ValueError("复核状态无效")
        if not all(isinstance(s, str) for s in (case_id, reason, note)) or len(reason) > 100 or len(note) > 2000:
            raise ValueError("复核文字过长或格式无效")
        record = self.get_run(run_id)
        if case_id not in {case["id"] for case in record["cases"]}:
            raise ValueError("未找到对应图片")
        entry = {"id": uuid.uuid4().hex, "run_id": run_id, "case_id": case_id, "status": status,
                 "reason": reason.strip(), "note": note.strip(), "created_at": utc_now(), "label_modified": False}
        self.workspace.mkdir(parents=True, exist_ok=True)
        with self.lock, (self.workspace / "reviews.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def review_csv(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["run_id", "case_id", "status", "reason", "note", "created_at", "label_modified"])
        for entry in self._reviews():
            writer.writerow([csv_safe(entry.get(key, "")) for key in ("run_id", "case_id", "status", "reason", "note", "created_at", "label_modified")])
        return ("\ufeff" + output.getvalue()).encode("utf-8")
