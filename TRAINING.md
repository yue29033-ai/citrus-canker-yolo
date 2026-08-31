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
  name=train-3
```

如果出现内存不足，把 `batch=16` 改为 `batch=8`，其余参数先不变。

训练过程中 YOLO 会使用 `val` 选择效果最好的权重。最终应使用：

`runs/detect/train-3/weights/best.pt`

而不是 `last.pt`。

## 3. 只做一次最终测试

训练完成并确定不再根据验证结果调参后，再运行：

```bash
yolo detect val \
  model=runs/detect/train-3/weights/best.pt \
  data=data.yaml \
  split=test \
  name=train-3-test
```

不要根据 test 结果反复改变训练参数，否则 test 就不再是独立测试集。

## 4. 用新图片试识别

把 `source` 换成一张图片或一个图片文件夹：

```bash
yolo detect predict \
  model=runs/detect/train-3/weights/best.pt \
  source="/你的图片或文件夹路径" \
  conf=0.25 \
  name=train-3-predict
```

## 5. 判断是否值得继续训练

- 先看 `runs/detect/train-3/results.png`：训练损失和验证损失应整体下降，而不是验证损失持续恶化。
- 再看 test 上的 Precision、Recall、mAP50 和 mAP50-95。
- 最后必须人工查看陌生叶片的预测图，确认病斑有没有漏检、叶尖枯斑或阴影有没有被误检。
- 如果训练集指标高但陌生叶片效果差，不要继续堆 epochs；优先增加新的物理叶片、自然背景和不同光照图片。
