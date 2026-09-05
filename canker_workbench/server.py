"""Loopback-only HTTP API using the existing Python environment."""

from __future__ import annotations

import base64
import binascii
import io
import json
import mimetypes
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

from .core import Workbench, inside


MAX_REQUEST = 57 * 1024 * 1024
MAX_IMAGE = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_BATCH_PIXELS = 50_000_000
STATIC_FILES = {"/": "simple.html", "/index.html": "simple.html", "/web/simple.js": "simple.js", "/web/simple.css": "simple.css",
                "/annotate": "annotate.html", "/web/annotate.js": "annotate.js", "/web/annotate.css": "annotate.css"}


def decode_upload(item, max_pixels=MAX_PIXELS):
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("data"), str):
        raise ValueError("上传文件格式无效")
    name = item["name"].replace("\\", "/").split("/")[-1]
    if not name or len(name) > 240:
        raise ValueError("文件名无效或过长")
    try:
        content = base64.b64decode(item["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片编码无效") from exc
    if not content or len(content) > MAX_IMAGE:
        raise ValueError("每张图片需小于 10 MB")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("只支持 JPEG、PNG 或 WebP 静态图片")
            if image.width * image.height > min(MAX_PIXELS, max_pixels) or getattr(image, "n_frames", 1) != 1:
                raise ValueError("单图不可超过 2500 万像素、整批不可超过 5000 万像素，且不能为动画")
            image.load()
            normalized = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片损坏或尺寸过大，请重新选择") from exc
    return name, content, normalized


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, workbench):
        super().__init__(address, Handler)
        self.workbench = workbench
        self.csrf_token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="canker-job")
        self.jobs = {}
        self.job_lock = threading.Lock()

    def submit(self, work):
        with self.job_lock:
            if any(job["status"] in ("queued", "running") for job in self.jobs.values()):
                raise ValueError("已有检测或评估正在运行，请完成后再开始")
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"status": "queued", "progress": {"done": 0, "total": 0, "message": "等待模型启动"}}

        def update(done, total, message):
            with self.job_lock:
                self.jobs[job_id]["progress"] = {"done": done, "total": total, "message": message}

        def execute():
            with self.job_lock:
                self.jobs[job_id]["status"] = "running"
            try:
                result = work(update)
                with self.job_lock:
                    self.jobs[job_id].update({"status": "completed", "run_id": result["id"]})
            except Exception as exc:
                with self.job_lock:
                    self.jobs[job_id].update({"status": "failed", "error": str(exc) or type(exc).__name__})

        self.executor.submit(execute)
        return job_id


class Handler(BaseHTTPRequestHandler):
    server_version = "CankerLocal/1.0"

    def log_message(self, format, *args):
        # Never log uploads, notes, tokens or local filesystem paths.
        return

    def send_bytes(self, data, content_type, status=200, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def valid_origin(self):
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        return origin is None or origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def do_GET(self):
        if not self.valid_host() or not self.valid_origin():
            self.send_json({"error": "仅允许本机页面访问"}, 403)
            return
        path = unquote(urlsplit(self.path).path)
        wb = self.server.workbench
        try:
            if path in STATIC_FILES:
                target = wb.root / "web" / STATIC_FILES[path]
                content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[target.suffix]
                self.send_bytes(target.read_bytes(), content_type + "; charset=utf-8")
            elif path == "/api/status":
                self.send_json({**wb.status(), "csrf_token": self.server.csrf_token})
            elif path == "/api/annotations":
                from .polygons import list_annotations
                self.send_json({"samples": list_annotations(wb.root)})
            elif path.startswith("/api/annotation/image/"):
                from .polygons import sample
                row = sample(wb.root, path.removeprefix("/api/annotation/image/"))
                self.send_bytes(row["path"].read_bytes(), mimetypes.guess_type(row["path"].name)[0] or "image/jpeg")
            elif path.startswith("/api/annotation/"):
                from .polygons import sample, load_annotation
                row = sample(wb.root, path.removeprefix("/api/annotation/"))
                self.send_json({"annotation": load_annotation(wb.root,row)})
            elif path == "/api/runs":
                self.send_json({"runs": wb.list_runs()})
            elif path.startswith("/api/runs/"):
                self.send_json(wb.get_run(path.removeprefix("/api/runs/")))
            elif path.startswith("/api/jobs/"):
                with self.server.job_lock:
                    job = self.server.jobs.get(path.removeprefix("/api/jobs/"))
                    self.send_json(job or {"error": "任务不存在"}, 200 if job else 404)
            elif path == "/api/reviews.csv":
                self.send_bytes(wb.review_csv(), "text/csv; charset=utf-8", attachment="review-history.csv")
            elif path.startswith("/files/"):
                parts = path.removeprefix("/files/").split("/", 1)
                if len(parts) != 2:
                    raise ValueError("文件地址不完整")
                target = inside(wb.run_dir(parts[0]), parts[1])
                if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".csv", ".json", ".md", ".zip", ".txt"}:
                    raise ValueError("文件类型不可访问")
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                attachment = "report" + target.suffix if target.suffix in {".csv", ".json", ".md", ".zip", ".txt"} else None
                self.send_bytes(target.read_bytes(), content_type, attachment=attachment)
            else:
                self.send_json({"error": "页面不存在"}, 404)
        except FileNotFoundError:
            self.send_json({"error": "记录或文件不存在"}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except OSError:
            self.send_json({"error": "无法读取本地文件，请检查目录权限"}, 500)

    def do_POST(self):
        token = self.headers.get("X-CSRF-Token", "")
        valid_token = token.isascii() and secrets.compare_digest(token, self.server.csrf_token)
        if not self.valid_host() or not self.valid_origin() or not valid_token:
            self.send_json({"error": "本地会话校验失败，请刷新页面"}, 403)
            return
        path = urlsplit(self.path).path
        wb = self.server.workbench
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                raise ValueError("上传内容为空或超过大小限制")
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("请求必须使用 JSON 格式")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("请求格式无效")
            if path == "/api/annotation":
                from .polygons import save_annotation
                self.send_json({"annotation": save_annotation(wb.root, data.get("id"), data.get("shapes"), data.get("complete",False), data.get("leaf_id",""))})
            elif path == "/api/predict":
                with self.server.job_lock:
                    if any(job["status"] in ("queued", "running") for job in self.server.jobs.values()):
                        raise ValueError("已有任务运行中，请完成后再上传")
                files = data.get("files")
                if not isinstance(files, list) or not 1 <= len(files) <= 12:
                    raise ValueError("每次请选择 1–12 张图片")
                overrides = data.get("settings", {})
                self.validate_settings(overrides, {"imgsz", "conf", "iou"})
                wb.settings(overrides)
                decoded, total_bytes, total_pixels = [], 0, 0
                for item in files:
                    decoded_item = decode_upload(item, MAX_BATCH_PIXELS - total_pixels)
                    total_bytes += len(decoded_item[1])
                    total_pixels += decoded_item[2].width * decoded_item[2].height
                    if total_bytes > 40 * 1024 * 1024:
                        raise ValueError("每次上传的图片总大小需小于 40 MB")
                    decoded.append(decoded_item)

                def work(progress):
                    folder = wb.workspace / "uploads" / uuid.uuid4().hex
                    folder.mkdir(parents=True, exist_ok=False)
                    paths, names = [], []
                    for index, (name, raw, image) in enumerate(decoded, 1):
                        with Image.open(io.BytesIO(raw)) as uploaded:
                            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[uploaded.format]
                        (folder / f"{index:04d}-original{suffix}").write_bytes(raw)
                        image_path = folder / f"{index:04d}.png"
                        image.save(image_path)
                        paths.append(image_path)
                        names.append(name)
                    return wb.predict(paths, overrides, progress, names)

                self.send_json({"job_id": self.server.submit(work)}, 202)
            elif path == "/api/demo":
                stems = ["Citrus Canker518", "Citrus Canker127", "Citrus Canker566"]
                images = list((wb.root / "dataset/images/test").iterdir())
                paths = [next((p for p in images if p.stem == stem), None) for stem in stems]
                if any(path is None for path in paths):
                    raise ValueError("示例图片缺失，请直接上传图片")
                self.send_json({"job_id": self.server.submit(lambda progress: wb.predict(paths, progress=progress))}, 202)
            elif path == "/api/evaluate":
                overrides = data.get("settings", {})
                self.validate_settings(overrides, {"split", "imgsz", "conf", "iou", "match_iou", "include_map"})
                wb.settings(overrides)
                self.send_json({"job_id": self.server.submit(lambda progress: wb.evaluate(overrides, progress))}, 202)
            elif path == "/api/review":
                self.send_json({"review": wb.review(data.get("run_id"), data.get("case_id"), data.get("status"), data.get("reason", ""), data.get("note", ""))})
            else:
                self.send_json({"error": "操作不存在"}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except OSError:
            self.send_json({"error": "无法保存本地记录，请检查目录权限和磁盘空间"}, 500)

    @staticmethod
    def validate_settings(settings, allowed):
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError("设置包含不支持的选项")


def serve(port=8765, open_browser=False):
    if not 1024 <= port <= 65535:
        raise ValueError("端口须在 1024–65535 之间")
    server = LocalServer(("127.0.0.1", port), Workbench())
    url = f"http://127.0.0.1:{port}"
    print(f"柑橘病斑工作台已启动：{url}\n仅本机可访问。按 Control+C 停止。", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止；已有记录保留。", flush=True)
    finally:
        server.server_close()
        server.executor.shutdown(wait=True)
