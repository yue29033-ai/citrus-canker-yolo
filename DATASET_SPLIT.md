# 数据集拆分记录

拆分日期：2026-08-31

目标：保留现有全部 401 张图片，同时避免同一片物理叶片跨训练、验证和测试集合。

## 当前拆分

- `test`：`Citrus Canker67–82`、`127–137`、`369–396`，共 55 张图片。
- `val`：`Citrus Canker201–235`、`271–300`，共 65 张图片。
- `train`：其余 `Citrus Canker1–396`，加上 `Citrus_canker (1)–(5)`，共 281 张图片。

比例约为：train 70.1%、val 16.2%、test 13.7%。

## 重复图片处理

`Citrus_canker (1)–(5).jpg` 是 `Citrus Canker1–5.jpeg` 的重压缩或近重复版本。按当前要求不删除，全部与原图一起放在 `train`，防止跨集合泄漏。

## 注意事项

- `Citrus Canker41.jpeg` 和 `Citrus Canker42.jpeg` 未见明确病斑，保留为空标签负样本。
- 当前独立物理叶片数量仍较少，val/test 指标只能作为阶段性参考。
- 后续新增图片时，应先按物理叶片编号分组，再决定整组进入哪个集合。

## 训练缓存

重新拆分前的 `train.cache` 和 `val.cache` 已归档至 `dataset/cache_archive/2026-08-31-before-resplit/`。下次训练时 YOLO 会根据当前数据重新建立缓存。
