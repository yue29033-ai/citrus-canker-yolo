# 柑橘病斑识别

## 当前简化版（2026-09-05）

按最新要求，网页只保留：选择一张照片 → 识别病斑 → 查看框选和数量 → 保存结果图。没有侧边导航、病例复核或评估对比页面。双击 `打开病斑工作台.command`，或访问正在运行的 `http://127.0.0.1:8765`。

面积比例尚未加入：它需要叶片与病斑的像素轮廓，不能用矩形框面积替代。病斑数量是模型检出的候选数量，不是人工确认的总数。

已加入独立的 `/annotate` 轮廓标注页（主页面底部链接），可保存草稿和完成状态。它只服务于下一步分割训练准备，不增加病例册或评估菜单。具体操作及导出见 [标注说明](segmentation_pilot/标注说明.md)。当前 10 张试标图仍需人工描轮廓，尚未训练分割模型。

以下保留初版工具的技术说明作为备查，**其中复核与评估不是当前网页功能，也不再继续开发**。已有代码、数据、运行记录均未删除。

当前仍使用现有 YOLO11n / train-5，不是新的训练模型。它只监听本机地址，不上传图片到云端，不修改原数据标签。

## 打开和关闭

在项目文件夹中双击 **打开病斑工作台.command**。浏览器打开 `http://127.0.0.1:8765`，启动时出现的终端窗口需要保持打开。完成后在该窗口按 Control+C 停止。

当前电脑直接复用 `/opt/miniconda3/bin/python`，不需要安装新依赖。如果系统不允许双击，或你想从终端启动：

```bash
cd /Users/dujiaoshourg/Desktop/citrus_canker_yolo
/opt/miniconda3/bin/python app.py --open
```

如果提示端口被占用，先访问上面的地址，工作台可能已启动。也可以另用 `python app.py --port 8766 --open`，不要结束不明进程。Mac 的启动文件也支持用 `CANKER_PYTHON` 指定已有 Python 环境；其他电脑可使用项目虚拟环境运行 `python app.py`。

## 三个页面怎么用

### 图片检测

1. 拖入或选择 JPEG、PNG、WebP 静态图片，单次 1–12 张；单张最多 10 MB，整批最多 40 MB。单图最多 2500 万像素、整批最多 5000 万像素。
2. 点击开始检测。首次使用要加载权重，通常比后续更慢；处理中不要关闭服务。
3. 选择结果缩略图，对比原图和预测图，查看每个候选病斑的局部放大、检测分数与耗时。
4. 下载结果 CSV、报告或 ZIP，或进入病例复核。

“试用示例”使用项目已有的三张诊断图片，包含典型病斑、困难阴性和困难漏检样本；它不是陌生图片上的测试成绩。上传图片的 EXIF 方向只在工作副本中规范化，原始上传字节另行保留。

**未检出溃疡病斑不等于健康，置信度也不是患病概率。** 当前默认 `960 / 0.55` 仅沿用历史演示参数，不承诺是 val 或新叶片上的最佳阈值。

### 病例复核

选择检测或评估记录，再按未复核、误报、漏检、待确认等条件筛选。查看原图、预测图和病斑局部；评估记录还提供绿色标签框与红色预测框的对照图。

人工填写“正确 / 存在误报 / 存在漏检 / 需要确认”、可能原因与说明。若同时误报和漏检，选主要问题并在说明补充另一项。保存时只追加独立记录，不会修改 `.txt` 标签，不会把图片自动加入训练。再次保存保留旧记录，界面显示最新一次判断。

未标注的上传图片没有自动判对错；评估中的自动误报/漏检只是相对于现有标签的统计，标签有疑问时请记为待确认。记录为功能测试的条目也不能当病害确认。

### 评估对比

默认选择 **val**。固定同一模型、数据和标注版本，选择 640 或 960 与置信度，运行后查看：

- 病斑级：TP（匹配正确）、FP（多报/定位不匹配）、FN（漏掉/定位不匹配），以及 Precision、Recall、F1。
- 图片级：阳性图片检出率、阴性图片误报率。图片只要有一个框就判为检出，因此这项指标不证明病斑位置正确。
- mAP：勾选“同时计算 mAP”后，另外调用 Ultralytics 标准验证流程，最低候选置信度为 0.001，跨置信度与 IoU 阈值统计。它不是固定阈值 Precision 的另一个名称。
- 推理耗时：模型执行时间，不包括上传、图像渲染、打包等，不应当作完整用户等待时间。

病斑固定阈值匹配采用 IoU ≥ 0.5 的最高 IoU 优先一对一匹配，NMS IoU 默认 0.7；这两个参数的用途不同。分母为零的指标显示不适用，不伪装为 0% 或 100%。缺少标签、非法坐标、孤立标签、图片损坏会报错，不能把缺标签自动当阴性。

当前 test 已参与错误分析及重标，只能选作阶段性诊断。工具不在 test 上自动选择阈值，不生成“外部测试”，也不重新拆分数据。最终独立评价仍需要新的物理叶片。

每次官方 mAP 验证只读取本次运行目录里的数据副本，缓存写在副本旁边，不会改原 dataset 缓存。固定阈值流程使用逐张预测，官方 mAP 使用验证器，二者预处理/统计流程应在比较时区分。

## 文件保存在哪里

```text
configs/workbench.json           固定模型与默认参数
canker_workbench/               共用推理、评估、复核、本地服务与数据审查
web/                            页面资源，不依赖 CDN
tests/                          自动化测试
workspace/                      本机运行区，不进入 Git
  uploads/<唯一编号>/           上传原件与方向规范化副本
  runs/<时间-唯一编号>/         每次检测/评估，不覆盖上次
    run.json                    参数、软件版本、权重摘要与病例
    images/ annotated/ crops/   原图副本、结果图、局部放大图
    comparisons/                评估时的标签/预测对照
    per_image.csv REPORT.md     逐图数据与报告
    metrics.json                评估指标与数据 SHA-256
    map_snapshot/ official_map/ 可选官方验证副本与曲线
    results.zip                 当次结果导出，不含数据快照缓存
  reviews.jsonl                 追加式人工复核历史
```

不会自动删除运行记录。批量评估会保存图片副本和结果图，勾选 mAP 还会生成独立快照，因此会占用额外空间。ZIP 是当次运行完成时的快照；后来追加的人工复核请单独导出复核 CSV。

## 命令行与复现

```bash
# 默认 val，固定阈值指标
python evaluate.py --split val --imgsz 960 --conf 0.55

# 同时产生官方 mAP、曲线和隔离缓存
python evaluate.py --split val --imgsz 960 --conf 0.55 --map

# 只读审查配对、坐标、损坏图片、精确重复和可选叶片索引
python -m canker_workbench.audit

# 自动化测试和旧版数据检查
python -m unittest discover -s tests -v
python validate_dataset.py
git diff --check
```

`requirements.txt` 已对齐本机实际使用的 Ultralytics 8.4.135。当前运行还会自动记录 Python、PyTorch、NumPy、Pillow 版本以及权重 SHA-256；评估记录额外保存选中集合的图片与标签内容摘要。不要在重现实验时静默升级环境。

权重默认在 `runs/detect/train-5/weights/best.pt`。仓库不包含该文件，复制代码到另一台机器时必须另行准备自己训练的或受信任来源的权重，并修改配置；程序不会自动联网下载权重。仅加载可信 `.pt` 文件。

可选人工叶片索引为 `dataset/manifests/leaf_index.csv`，字段 `image_path,leaf_id,source`；路径写项目相对路径，例如 `dataset/images/train/叶片照片.jpg`。只有确认属于同一物理叶片才使用相同编号。当前没有此索引，工具如实显示未知；字节哈希没有重复不代表没有同叶的旋转、重拍或重编码版本。

## English summary

The local workbench reuses YOLO11n / train-5 for batch inference, human review, and reproducible evaluation. Run `python app.py --open`, then visit `http://127.0.0.1:8765`. No cloud service or new global package is required on the current machine.

Reviews are append-only records, never automatic training labels. Each run stores its settings, dependency versions, checkpoint hash and exports in the ignored `workspace/` directory. Fixed-confidence lesion metrics and image-level false-positive rates are reported separately from optional official mAP. Standard validation uses copied data and isolated caches; original images and annotations are untouched.

The existing test split is development diagnostic only. The default confidence 0.55 is a historical demonstration setting, not an independently validated optimum. No detection does not mean a healthy leaf, and model confidence is not a calibrated disease probability.
