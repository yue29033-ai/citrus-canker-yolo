"""Human polygon annotations and checked, isolated YOLO-seg exports."""
import csv
import json
import math
import shutil
import threading
import uuid
from pathlib import Path
from PIL import Image, ImageDraw, ImageChops
from .core import inside, sha256, utc_now, write_json

LOCK = threading.RLock()


def samples(root):
    root = Path(root)
    with (root / "segmentation_pilot/selection.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    result = []
    for row in rows:
        if row["original_split"] not in {"train", "val"}:
            raise ValueError("试标集合必须是 train 或 val")
        path = inside(root / "segmentation_pilot/images", row["original_split"] + "/" + row["filename"])
        result.append({**row, "id": row["sha256"][:16], "path": path})
    if len({row["id"] for row in result}) != len(result):
        raise ValueError("试标清单存在重复编号")
    return result


def sample(root, sample_id):
    for row in samples(root):
        if row["id"] == sample_id:
            return row
    raise ValueError("找不到试标图片")


def annotation_path(root, row):
    return inside(Path(root) / "segmentation_pilot/annotations", row["original_split"] + "/" + Path(row["filename"]).stem + ".json")


def load_annotation(root, row):
    path = annotation_path(root, row)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def list_annotations(root):
    output = []
    for row in samples(root):
        annotation = load_annotation(root, row)
        output.append({"id": row["id"], "name": row["filename"], "split": row["original_split"],
                       "complete": bool(annotation and annotation.get("flags", {}).get("complete")),
                       "saved": annotation is not None, "image_url": "/api/annotation/image/" + row["id"]})
    return output


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def intersects(a, b, c, d):
    x, y, z, w = cross(a,b,c), cross(a,b,d), cross(c,d,a), cross(c,d,b)
    if x*y < 0 and z*w < 0:
        return True
    def on(a,b,p):
        return min(a[0],b[0]) <= p[0] <= max(a[0],b[0]) and min(a[1],b[1]) <= p[1] <= max(a[1],b[1])
    return any(abs(v)<1e-8 and on(p,q,r) for v,p,q,r in [(x,a,b,c),(y,a,b,d),(z,c,d,a),(w,c,d,b)])


def validate_shapes(shapes, width, height, complete=False):
    if not isinstance(shapes, list) or len(shapes) > 150:
        raise ValueError("轮廓数量无效")
    checked = []
    for shape in shapes:
        if not isinstance(shape, dict) or shape.get("label") not in ("leaf", "canker") or shape.get("shape_type", "polygon") != "polygon":
            raise ValueError("只支持 leaf 和 canker 多边形")
        points = shape.get("points")
        if not isinstance(points, list) or not 3 <= len(points) <= 500:
            raise ValueError("每条轮廓需要 3–500 个点")
        clean = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("轮廓坐标格式无效")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
                raise ValueError("轮廓坐标必须是有限数字")
            x, y = point
            if not 0 <= x < width or not 0 <= y < height:
                raise ValueError("轮廓点超出图片")
            clean.append([float(x), float(y)])
        n = len(clean)
        if len({tuple(p) for p in clean}) != n:
            raise ValueError("轮廓包含重复点；首尾不必重复点击")
        area = abs(sum(clean[i][0]*clean[(i+1)%n][1]-clean[(i+1)%n][0]*clean[i][1] for i in range(n)))/2
        if area < 1:
            raise ValueError("轮廓面积过小或共线")
        for i in range(n):
            for j in range(i+1,n):
                if j == i+1 or (i == 0 and j == n-1):
                    continue
                if intersects(clean[i],clean[(i+1)%n],clean[j],clean[(j+1)%n]):
                    raise ValueError("轮廓线交叉了，请撤回后重新描绘")
        checked.append({"label": shape["label"], "points": clean, "group_id": None, "shape_type": "polygon", "flags": {}})
    leaves = [s for s in checked if s["label"] == "leaf"]
    if len(leaves) > 1 or (complete and len(leaves) != 1):
        raise ValueError("每张图片需要且只需要一个完整叶片轮廓")
    if complete:
        leaf = Image.new("L", (width, height)); ImageDraw.Draw(leaf).polygon([tuple(p) for p in leaves[0]["points"]], fill=255)
        for shape in checked:
            if shape["label"] == "canker":
                lesion = Image.new("L", (width,height)); ImageDraw.Draw(lesion).polygon([tuple(p) for p in shape["points"]], fill=255)
                if ImageChops.subtract(lesion,leaf).getbbox():
                    raise ValueError("病斑轮廓超出了整叶轮廓，请检查边缘")
    return checked


def save_annotation(root, sample_id, shapes, complete=False, leaf_id=""):
    root = Path(root)
    if type(complete) is not bool or not isinstance(leaf_id,str) or len(leaf_id)>100:
        raise ValueError("完成状态或叶片编号无效")
    with LOCK:
        row = sample(root, sample_id)
        if sha256(row["path"]) != row["sha256"]:
            raise ValueError("图片已变化，请先核对试标清单")
        with Image.open(row["path"]) as image:
            width, height = image.size
            if image.getexif().get(274,1) != 1:
                raise ValueError("图片有方向信息，需先核对方向后标注")
        checked = validate_shapes(shapes, width, height, complete)
        if complete and not leaf_id.strip():
            raise ValueError("请填写叶片编号；同一片叶子的照片使用同一编号")
        value = {"version": "5.6.1", "flags": {"complete": complete}, "shapes": checked,
                 "imagePath": row["filename"], "imageData": None, "imageHeight": height, "imageWidth": width,
                 "leaf_id": leaf_id.strip(), "image_sha256": row["sha256"], "saved_at": utc_now()}
        path = annotation_path(root,row); path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            archive = root / "segmentation_pilot/annotations/history"; archive.mkdir(exist_ok=True)
            shutil.copy2(path, archive / f"{sample_id}-{uuid.uuid4().hex}.json")
        write_json(path,value)
        return value


def export_dataset(root):
    root = Path(root).resolve()
    with LOCK:
        prepared, groups = [], {}
        for row in samples(root):
            data = load_annotation(root,row)
            if not data or data.get("flags",{}).get("complete") is not True:
                raise ValueError(f"尚未确认完成：{row['filename']}")
            with Image.open(row["path"]) as image: width,height=image.size
            if data.get("imageWidth") != width or data.get("imageHeight") != height or data.get("image_sha256") != row["sha256"] or sha256(row["path"]) != row["sha256"]:
                raise ValueError(f"图片与轮廓版本不一致：{row['filename']}")
            shapes = validate_shapes(data["shapes"],width,height,True)
            leaf_id = data.get("leaf_id","").strip()
            if not leaf_id: raise ValueError("有图片缺少物理叶片编号")
            if leaf_id in groups and groups[leaf_id] != row["original_split"]:
                raise ValueError(f"同一片叶子跨集合：{leaf_id}，请先确认并调整正式划分")
            groups[leaf_id] = row["original_split"]
            prepared.append((row,data,shapes,width,height))
        if not prepared or {r[0]["original_split"] for r in prepared} != {"train","val"}:
            raise ValueError("需要训练和验证两组标注")
        output = root / "workspace/segmentation_exports" / uuid.uuid4().hex
        output.mkdir(parents=True,exist_ok=False)
        manifest = []
        for row,data,shapes,width,height in prepared:
            image_dir = output / "images" / row["original_split"]; image_dir.mkdir(parents=True,exist_ok=True)
            label_dir = output / "labels" / row["original_split"]; label_dir.mkdir(parents=True,exist_ok=True)
            shutil.copy2(row["path"],image_dir / row["filename"])
            lines = []
            for shape in shapes:
                coords = " ".join(f"{x/width:.8f} {y/height:.8f}" for x,y in shape["points"])
                lines.append(f"{0 if shape['label']=='leaf' else 1} {coords}")
            (label_dir / (Path(row["filename"]).stem+".txt")).write_text("\n".join(lines)+"\n",encoding="utf-8")
            manifest.append({"filename": row["filename"], "split": row["original_split"], "leaf_id": data["leaf_id"], "image_sha256": row["sha256"], "annotation_sha256": sha256(annotation_path(root,row))})
        write_json(output / "data.yaml", {"path":str(output),"train":"images/train","val":"images/val","names":{0:"leaf",1:"canker"}})
        write_json(output / "manifest.json",manifest)
        return output
