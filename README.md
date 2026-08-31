# Citrus Canker Detection with YOLO / 基于 YOLO 的柑橘溃疡病斑检测

[中文](#中文说明) | [English](#english)

## 中文说明

### 项目简介

本项目使用 Ultralytics YOLO11 检测甜橙叶片上的柑橘溃疡病斑，面向人工智能通识课算法实践。当前任务为单类别目标检测：

- 类别 `0`：`canker`（柑橘溃疡病斑）
- 基础模型：YOLO11n
- 输入：甜橙叶片图片
- 输出：病斑位置、类别与置信度

### 数据集

当前数据集包含 401 张图片和 455 个标注框：

| 集合 | 图片数 | 标注框数 | 用途 |
| --- | ---: | ---: | --- |
| train | 281 | 313 | 模型训练 |
| val | 65 | 65 | 模型选择与调参 |
| test | 55 | 77 | 最终独立评估 |

数据不是按单张图片随机拆分，而是按物理叶片分组：同一片叶子的旋转、距离和角度变化只会出现在一个集合中，以减少数据泄漏。根据当前实验要求，5 张重压缩或近重复图片仍被保留，并与对应原图统一放在训练集。

数据来源于甜橙叶片病害数据，经人工筛选、重新命名、YOLO 格式标注和按叶片分组。若要将仓库改为公开，请先确认原始数据集的再分发许可并补充正式引用信息。

### 项目结构

```text
citrus-canker-yolo/
├── dataset/
│   ├── images/{train,val,test}/
│   └── labels/{train,val,test}/
├── data.yaml
├── validate_dataset.py
├── TRAINING.md
├── DATASET_SPLIT.md
└── requirements.txt
```

训练输出、缓存和模型权重不会提交到 Git。完整拆分规则见 [DATASET_SPLIT.md](DATASET_SPLIT.md)。

### 环境安装

建议使用 Python 3.10 或更高版本：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 数据校验

```bash
python validate_dataset.py
```

校验脚本会检查图片与标签是否配对、类别编号、YOLO 坐标范围、空标签以及各集合数量。

### 训练

```bash
yolo detect train \
  model=yolo11n.pt \
  data=data.yaml \
  epochs=80 \
  imgsz=640 \
  batch=16 \
  patience=15 \
  cache=False \
  name=train-3
```

数据划分改变后应从 `yolo11n.pt` 重新训练，不要使用旧实验的 `last.pt` 续训。训练结束后优先使用 `runs/detect/train-3/weights/best.pt`。

### 最终测试

```bash
yolo detect val \
  model=runs/detect/train-3/weights/best.pt \
  data=data.yaml \
  split=test \
  name=train-3-test
```

test 集只用于最终评估，不应根据 test 结果反复调参。更详细的操作说明见 [TRAINING.md](TRAINING.md)。

### 当前限制

- 独立物理叶片数量仍然有限，指标只能作为阶段性结果。
- 拍摄背景以白色背景为主，实际果园环境下的泛化能力尚未验证。
- 后续应优先增加新的叶片、自然背景、不同光照和不同病斑阶段，而不是只增加同一片叶子的旋转图片。

## English

### Overview

This project uses Ultralytics YOLO11 to detect citrus canker lesions on sweet-orange leaves. It was developed as an algorithm practice project for a general artificial intelligence course. The current task is single-class object detection:

- Class `0`: `canker`
- Base model: YOLO11n
- Input: sweet-orange leaf images
- Output: lesion bounding boxes, class labels, and confidence scores

### Dataset

The current dataset contains 401 images and 455 annotated bounding boxes:

| Split | Images | Boxes | Purpose |
| --- | ---: | ---: | --- |
| train | 281 | 313 | Model training |
| val | 65 | 65 | Model selection and tuning |
| test | 55 | 77 | Final independent evaluation |

The dataset is grouped by physical leaf instead of being randomly split image by image. Different rotations, distances, and viewing angles of the same leaf are kept within one split to reduce data leakage. Five recompressed or near-duplicate images are intentionally retained for the current experiment and placed in the training split together with their corresponding originals.

The images were prepared from a sweet-orange leaf disease dataset and were manually selected, renamed, annotated in YOLO format, and grouped by leaf. Before making this repository public, verify the redistribution terms of the original dataset and add its formal citation.

### Repository Structure

```text
citrus-canker-yolo/
├── dataset/
│   ├── images/{train,val,test}/
│   └── labels/{train,val,test}/
├── data.yaml
├── validate_dataset.py
├── TRAINING.md
├── DATASET_SPLIT.md
└── requirements.txt
```

Training outputs, caches, and model weights are excluded from Git. See [DATASET_SPLIT.md](DATASET_SPLIT.md) for the complete split policy.

### Installation

Python 3.10 or later is recommended:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Dataset Validation

```bash
python validate_dataset.py
```

The validation script checks image-label pairing, class IDs, normalized YOLO coordinates, empty labels, and split sizes.

### Training

```bash
yolo detect train \
  model=yolo11n.pt \
  data=data.yaml \
  epochs=80 \
  imgsz=640 \
  batch=16 \
  patience=15 \
  cache=False \
  name=train-3
```

After changing the dataset split, start a fresh run from `yolo11n.pt` instead of resuming an old `last.pt`. Use `runs/detect/train-3/weights/best.pt` after training.

### Final Evaluation

```bash
yolo detect val \
  model=runs/detect/train-3/weights/best.pt \
  data=data.yaml \
  split=test \
  name=train-3-test
```

Use the test split only for final evaluation, not for repeated parameter tuning. See [TRAINING.md](TRAINING.md) for additional guidance.

### Current Limitations

- The number of independent physical leaves is still limited, so metrics should be treated as preliminary.
- Most images use a white background; generalization to real orchard environments has not yet been validated.
- Future data collection should prioritize new leaves, natural backgrounds, varied lighting, and different disease stages rather than additional rotations of the same leaves.
