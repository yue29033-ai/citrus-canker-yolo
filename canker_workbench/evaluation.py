"""Read-only dataset evaluation with explicit, fixed-threshold metrics."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def csv_safe(value):
    """Keep user-controlled filenames from becoming spreadsheet formulas."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def box_iou(first: list[float], second: list[float]) -> float:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area1 = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    area2 = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    return intersection / (area1 + area2 - intersection) if area1 + area2 > intersection else 0.0


def match_detections(ground_truth: list[dict], detections: list[dict], threshold: float = 0.5) -> dict:
    """Deterministic greedy highest-IoU one-to-one matching, single class."""
    if not 0 < threshold <= 1:
        raise ValueError("匹配 IoU 必须大于 0 且不超过 1。")
    candidates = []
    for gi, truth in enumerate(ground_truth):
        for pi, detection in enumerate(detections):
            overlap = box_iou(truth["xyxy"], detection["xyxy"])
            if overlap >= threshold:
                candidates.append((-overlap, gi, pi))
    used_gt, used_pred, matched = set(), set(), []
    for negative_iou, gi, pi in sorted(candidates):
        if gi not in used_gt and pi not in used_pred:
            used_gt.add(gi)
            used_pred.add(pi)
            matched.append({"ground_truth_index": gi, "prediction_index": pi, "iou": -negative_iou})
    return {"tp": len(matched), "fp": len(detections) - len(matched),
            "fn": len(ground_truth) - len(matched), "matched": matched}


def read_ground_truth(label_path: Path, width: int, height: int) -> list[dict]:
    """Missing, malformed or out-of-bounds labels are errors, not negatives."""
    if not label_path.is_file():
        raise ValueError(f"缺少标签，不能按阴性处理：{label_path.name}")
    boxes = []
    for line_number, line in enumerate(label_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        message = f"无效标签 {label_path.name}:{line_number}"
        if len(parts) != 5 or parts[0] != "0":
            raise ValueError(f"{message}：需要类别 0 和四个 YOLO 坐标。")
        try:
            x, y, w, h = map(float, parts[1:])
        except ValueError as exc:
            raise ValueError(f"{message}：坐标不是数字。") from exc
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y, w, h)) or w <= 0 or h <= 0:
            raise ValueError(f"{message}：坐标必须有限、归一化且框宽高大于零。")
        corners = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        # Six-decimal YOLO serialization can put a border off by < one millionth.
        if min(corners) < -1e-6 or max(corners) > 1 + 1e-6:
            raise ValueError(f"{message}：框边缘超出图片。")
        x1, y1, x2, y2 = (min(1.0, max(0.0, value)) for value in corners)
        boxes.append({"class_id": 0, "xyxy": [x1 * width, y1 * height, x2 * width, y2 * height]})
    return boxes


def _dataset_hash(records: list[dict], project_root: Path) -> str:
    digest = hashlib.sha256()
    for record in records:
        for field in ("image_path", "label_path"):
            path = record[field]
            digest.update(path.relative_to(project_root).as_posix().encode("utf-8") + b"\0")
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
            digest.update(content.digest())
    return digest.hexdigest()


def _collect_records(project_root: Path, split: str) -> list[dict]:
    image_dir = project_root / "dataset" / "images" / split
    label_dir = project_root / "dataset" / "labels" / split
    if not image_dir.is_dir() or not label_dir.is_dir():
        raise ValueError(f"{split} 图片或标签目录不存在。")
    image_paths = sorted(p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    if not image_paths:
        raise ValueError(f"{split} 没有可评估的图片。")
    stems = [p.stem for p in image_paths]
    if len(stems) != len(set(stems)):
        raise ValueError("同一集合存在同名、不同扩展名图片，无法唯一配对标签。")
    orphan = sorted(p.name for p in label_dir.glob("*.txt") if p.stem not in set(stems))
    if orphan:
        raise ValueError(f"存在没有图片的标签：{', '.join(orphan[:5])}")
    records = []
    for path in image_paths:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if image.getexif().get(274, 1) != 1:
                raise ValueError(f"{path.name} 带旋转 EXIF，需先确认标签与像素方向一致。")
        label_path = label_dir / f"{path.stem}.txt"
        records.append({"image_path": path, "label_path": label_path, "width": width,
                        "height": height, "ground_truth": read_ground_truth(label_path, width, height)})
    return records


def _comparison(record: dict, case: dict, run_dir: Path) -> str:
    target = run_dir / "comparisons"
    target.mkdir(parents=True, exist_ok=True)
    with Image.open(record["image_path"]) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    line_width = max(2, round(image.width / 400))
    for truth in record["ground_truth"]:
        draw.rectangle(truth["xyxy"], outline="#00aa55", width=line_width)
    for detection in case["detections"]:
        draw.rectangle(detection["xyxy"], outline="#e63746", width=line_width)
    image.thumbnail((1600, 1600))
    canvas = Image.new("RGB", (max(image.width, 480), image.height + 50), "white")
    canvas.paste(image, (0, 50))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 8), "Green: ground truth | Red: prediction", fill="black")
    values = case["metrics"]
    draw.text((10, 28), f"TP {values['tp']} | FP {values['fp']} | FN {values['fn']}", fill="black")
    output = target / f"{case['id']}.jpg"
    canvas.save(output, quality=90)
    return output.relative_to(run_dir).as_posix()


def _official_map(project_root: Path, run_dir: Path, settings: dict, records: list[dict]) -> tuple[dict, dict]:
    """Copy selected data; Ultralytics is never given the source dataset paths."""
    snapshot = run_dir / "map_snapshot"
    images, labels = snapshot / "images" / settings["split"], snapshot / "labels" / settings["split"]
    images.mkdir(parents=True, exist_ok=False)
    labels.mkdir(parents=True, exist_ok=False)
    for record in records:
        shutil.copy2(record["image_path"], images / record["image_path"].name)
        shutil.copy2(record["label_path"], labels / record["label_path"].name)
    data = {"path": str(snapshot.resolve()), "train": f"images/{settings['split']}",
            "val": f"images/{settings['split']}", "test": f"images/{settings['split']}", "names": {0: "canker"}}
    data_path = snapshot / "data.yaml"
    # JSON is valid YAML and correctly quotes paths on all platforms.
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    from ultralytics import YOLO
    weights = Path(settings["weights"])
    if not weights.is_absolute():
        weights = project_root / weights
    if not weights.is_file():
        raise ValueError("mAP 评估权重不存在；不会自动下载。")
    model = YOLO(str(weights))
    result = model.val(data=str(data_path), split=settings["split"], imgsz=settings["imgsz"],
                       conf=0.001, iou=settings["iou"], batch=1, workers=0, device=settings.get("device", "cpu"),
                       max_det=settings.get("max_det", 300), plots=True, save_json=False, project=str(run_dir / "official_map"),
                       name="validation", exist_ok=False, verbose=False)
    metrics = {"map50": float(result.box.map50), "map50_95": float(result.box.map),
               "minimum_confidence": 0.001, "method": "Ultralytics official detection validation"}
    artifacts = {}
    for name, path in (("map_pr_curve", "BoxPR_curve.png"), ("map_f1_curve", "BoxF1_curve.png"),
                       ("map_confusion_matrix", "confusion_matrix.png")):
        artifact = Path(result.save_dir) / path
        if artifact.is_file():
            artifacts[name] = artifact.relative_to(run_dir).as_posix()
    return metrics, artifacts


def evaluate_dataset(project_root: Path, run_dir: Path, settings: dict,
                     predict_image: Callable, progress: Callable | None = None) -> dict:
    project_root, run_dir = Path(project_root).resolve(), Path(run_dir).resolve()
    settings = {"split": "val", "imgsz": 960, "conf": 0.55, "iou": 0.7,
                "match_iou": 0.5, "include_map": False, **settings}
    if settings["split"] not in {"val", "test"}:
        raise ValueError("评估集合只支持 val 或 test。")
    if settings["imgsz"] not in {640, 960}:
        raise ValueError("评估图片尺寸只支持 640 或 960。")
    for key in ("conf", "iou", "match_iou"):
        value = settings[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError(f"{key} 必须大于 0 且不超过 1。")
    records = _collect_records(project_root, settings["split"])
    dataset_sha256 = _dataset_hash(records, project_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    if (run_dir / "metrics.json").exists() or (run_dir / "per_image.csv").exists():
        raise ValueError("该运行目录已有评估结果，请创建新的运行目录。")
    cases = []
    lesion = {"tp": 0, "fp": 0, "fn": 0}
    image_metrics = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for index, record in enumerate(records, 1):
        case_id = f"case_{index:04d}"
        case = predict_image(record["image_path"], case_id, run_dir, settings)
        case["id"] = case_id
        if (case["width"], case["height"]) != (record["width"], record["height"]):
            raise ValueError(f"{record['image_path'].name} 的预测尺寸与标签尺寸不一致。")
        detections = []
        for detection in case["detections"]:
            if detection.get("class_id", 0) != 0:
                raise ValueError("当前评估仅支持类别 0: canker，预测返回了其他类别。")
            coordinates, confidence = detection["xyxy"], detection["confidence"]
            if len(coordinates) != 4 or not all(math.isfinite(v) for v in coordinates) or not math.isfinite(confidence):
                raise ValueError("预测包含无效坐标或置信度。")
            if not 0 <= confidence <= 1 or coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
                raise ValueError("预测框宽高或置信度无效。")
            if confidence >= settings["conf"]:
                detections.append(detection)
        case["detections"], case["count"] = detections, len(detections)
        case["ground_truth"] = record["ground_truth"]
        case["metrics"] = match_detections(record["ground_truth"], detections, settings["match_iou"])
        values = case["metrics"]
        case["error_type"] = "both" if values["fp"] and values["fn"] else "false_positive" if values["fp"] else "false_negative" if values["fn"] else "none"
        case["comparison"] = _comparison(record, case, run_dir)
        for field in lesion:
            lesion[field] += values[field]
        actual_positive, predicted_positive = bool(record["ground_truth"]), bool(detections)
        decision = ("tp" if predicted_positive else "fn") if actual_positive else ("fp" if predicted_positive else "tn")
        image_metrics[decision] += 1
        cases.append(case)
        if progress:
            progress(index, len(records), f"已评估 {index}/{len(records)} 张：{record['image_path'].name}")
    if _dataset_hash(records, project_root) != dataset_sha256:
        raise RuntimeError("评估期间数据集发生变化，本次结果不能作为同一数据版本的评估。")
    lesion.update(precision=safe_ratio(lesion["tp"], lesion["tp"] + lesion["fp"]),
                  recall=safe_ratio(lesion["tp"], lesion["tp"] + lesion["fn"]),
                  f1=safe_ratio(2 * lesion["tp"], 2 * lesion["tp"] + lesion["fp"] + lesion["fn"]))
    positive = image_metrics["tp"] + image_metrics["fn"]
    negative = image_metrics["tn"] + image_metrics["fp"]
    image_metrics.update(positive_count=positive, negative_count=negative,
                         sensitivity=safe_ratio(image_metrics["tp"], positive),
                         specificity=safe_ratio(image_metrics["tn"], negative),
                         false_positive_rate=safe_ratio(image_metrics["fp"], negative))
    metrics = {"lesion": lesion, "image": image_metrics,
               "timing": {"mean_inference_ms": sum(float(c["inference_ms"]) for c in cases) / len(cases)},
               "image_count": len(cases), "settings": settings}
    warning = ("当前 test 已参与错误分析和重标，仅作阶段性诊断，不能作为最终独立测试成绩。"
               if settings["split"] == "test" else "val 用于模型选择与诊断；当前项目尚无独立 external_test 成绩。")
    artifacts = {"per_image_csv": "per_image.csv", "metrics_json": "metrics.json", "report": "REPORT.md"}
    if settings["include_map"]:
        if progress:
            progress(len(records), len(records), "正在隔离快照上计算官方跨阈值 mAP。")
        metrics["map"], map_artifacts = _official_map(project_root, run_dir, settings, records)
        artifacts.update(map_artifacts)
        if _dataset_hash(records, project_root) != dataset_sha256:
            raise RuntimeError("计算 mAP 期间源数据发生变化，请使用稳定的数据版本重新评估。")
    fields = ["id", "name", "ground_truth_count", "prediction_count", "tp", "fp", "fn", "error_type", "inference_ms", "comparison"]
    with (run_dir / "per_image.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            writer.writerow({"id": case["id"], "name": csv_safe(case["name"]), "ground_truth_count": len(case["ground_truth"]),
                             "prediction_count": case["count"], **{key: case["metrics"][key] for key in ("tp", "fp", "fn")},
                             "error_type": case["error_type"], "inference_ms": case["inference_ms"], "comparison": case["comparison"]})
    output = {"cases": cases, "metrics": metrics, "artifacts": artifacts,
              "dataset_sha256": dataset_sha256, "warning": warning}
    (run_dir / "metrics.json").write_text(json.dumps({key: value for key, value in output.items() if key != "cases"},
                                                     ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    def formatted(value):
        return "不适用（分母为 0）" if value is None else f"{value:.4f}"
    report = ["# 柑橘溃疡病检测评估", "", warning, "",
              f"集合：{settings['split']}；图片：{len(cases)}；尺寸：{settings['imgsz']}；置信度：{settings['conf']}；NMS IoU：{settings['iou']}；匹配 IoU：{settings['match_iou']}。", "",
              "## 固定阈值结果", "", f"病斑 TP / FP / FN：{lesion['tp']} / {lesion['fp']} / {lesion['fn']}。",
              f"病斑 Precision：{formatted(lesion['precision'])}；Recall：{formatted(lesion['recall'])}；F1：{formatted(lesion['f1'])}。", "",
              f"阳性图片：{positive}；阴性图片：{negative}；图片级灵敏度：{formatted(image_metrics['sensitivity'])}；阴性图片误报率：{formatted(image_metrics['false_positive_rate'])}。", "",
              "图片级阳性判定为至少有一个超过阈值的预测框，与是否准确定位病斑分开统计；错误图筛选使用病斑 IoU 匹配结果。病斑匹配采用 IoU 从高到低的一对一贪心匹配。", "",
              f"平均模型推理耗时：{metrics['timing']['mean_inference_ms']:.2f} ms/张，不包括文件处理、图片渲染和报告保存。", "",
              "## 跨阈值 mAP", ""]
    if "map" in metrics:
        report.append(f"官方 mAP50：{metrics['map']['map50']:.4f}；mAP50-95：{metrics['map']['map50_95']:.4f}。使用隔离数据快照和最低置信度 0.001，不能与上方固定阈值 Precision 混为一谈。")
    else:
        report.append("本次未计算 mAP。固定阈值的 Precision、Recall、F1 不代表 mAP。")
    report.extend(["", "## 数据版本与复核", "", f"SHA-256：`{dataset_sha256}`。该摘要覆盖本次选定集合的文件名、图片内容和标签内容。", "",
                   "数据与标签保持原样；绿色框为现有标签，红色框为模型预测。无框表示未检出溃疡病斑，不等同于叶片健康。", "",
                   "[逐图 CSV](per_image.csv) · [指标 JSON](metrics.json)", ""])
    (run_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    return output
