# 继续训练指南

## 1. 进入项目

```bash
cd /path/to/citrus-canker-yolo
```

## 2. 重新训练

本次数据划分已改变，不要用旧的 `last.pt` 续训。应从项目内的预训练权重 `yolo11n.pt` 开始一个新实验：

```bash
yolo detect train \
  model=yolo11n.pt \
  data=data.yaml \
  epochs=80 \
  imgsz=640 \
  batch=16 \
  patience=15 \
  cache=False \
  workers=0 \
  name=train-5
```

如果出现内存不足，把 `batch=16` 改为 `batch=8`，其余参数先不变。

训练过程中 YOLO 会使用 `val` 选择效果最好的权重。最终应使用：

`runs/detect/train-5/weights/best.pt`

而不是 `last.pt`。

## 3. 当前 test 与最终外部测试

当前 `test` 已经用于 train-4 的错误分析，并据此加入了困难负样本，因此它可以继续作为阶段性诊断集，但不能再宣称为完全未见的最终考试卷。train-5 训练过程中只根据 `val` 选模型，不根据当前 test 反复调参。

需要做阶段性对比时运行：

```bash
yolo detect val \
  model=runs/detect/train-5/weights/best.pt \
  data=data.yaml \
  split=test \
  workers=0 \
  name=train-5-test
```

最终汇报前，应另外收集一组来自新物理叶片、不同来源和不同背景的 `external_test`。先固定模型和置信度阈值，再一次性评估这组图片。

## 4. 用新图片试识别

把 `source` 换成一张图片或一个图片文件夹：

```bash
yolo detect predict \
  model=runs/detect/train-5/weights/best.pt \
  source="/你的图片或文件夹路径" \
  imgsz=960 \
  conf=0.55 \
  workers=0 \
  name=train-5-predict
```

临时找来的陌生图片放在 `demo_images/`，只用于演示或观察，不要随手混入正式 test。最终外部测试图片需要人工标注，才能计算 Precision、Recall 和 mAP。

以上是当前诊断集的演示参数，并非独立外部验证后的最佳阈值。现在也可双击 `打开病斑工作台.command` 使用本地检测页面，或通过 `python evaluate.py --split val --map` 运行统一评估。详见 [WORKBENCH.md](WORKBENCH.md)。

## 5. 判断是否值得继续训练

- 先看 `runs/detect/train-5/results.png`：训练损失和验证损失应整体下降，而不是验证损失持续恶化。
- 再看当前诊断 test 以及最终 external test 上的 Precision、Recall、mAP50 和 mAP50-95，并明确区分两者。
- 最后必须人工查看陌生叶片的预测图，确认病斑有没有漏检、叶尖枯斑或阴影有没有被误检。
- 如果训练集指标高但陌生叶片效果差，不要继续堆 epochs；优先增加新的物理叶片、自然背景和不同光照图片。
