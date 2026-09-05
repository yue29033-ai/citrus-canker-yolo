"""Validate completed manual polygons and export a separate YOLO-seg dataset."""
from pathlib import Path
from canker_workbench.polygons import export_dataset

if __name__ == "__main__":
    try:
        output = export_dataset(Path(__file__).resolve().parent)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(f"已导出：{output}\n训练配置：{output / 'data.yaml'}\n未启动训练；分割训练应设置 overlap_mask=False。")
