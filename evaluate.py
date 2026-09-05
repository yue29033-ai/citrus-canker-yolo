"""Evaluate the configured local model without changing source labels."""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="柑橘溃疡病固定阈值评估；test 仅作阶段性诊断。")
    parser.add_argument("--split", choices=("val", "test"), default="val")
    parser.add_argument("--imgsz", type=int, choices=(640, 960), default=960)
    parser.add_argument("--conf", type=float, default=0.55)
    parser.add_argument("--iou", type=float, default=0.7, help="预测框去重的 NMS IoU")
    parser.add_argument("--match-iou", type=float, default=0.5, help="评估真值与预测框的匹配 IoU")
    parser.add_argument("--weights", help="本地权重路径；未指定则使用工作台配置")
    parser.add_argument("--map", action="store_true", dest="include_map", help="另在隔离快照上运行官方 mAP 验证")
    args = parser.parse_args()
    settings = vars(args)
    if settings["weights"] is None:
        settings.pop("weights")
    from canker_workbench.core import Workbench
    workbench = Workbench(Path(__file__).resolve().parent)
    result = workbench.evaluate(settings)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
