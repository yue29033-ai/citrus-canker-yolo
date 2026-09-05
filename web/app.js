"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const state = { csrf: "", ready: false, busy: false, files: [], runs: [], detectRun: null, reviewRun: null, detectCase: null, reviewCase: null, reviewSaving: false, loadToken: { detect: 0, review: 0 } };
const STATUS_NAMES = { correct: "已确认正确", false_positive: "存在误报", false_negative: "存在漏检", uncertain: "待确认" };
const ARTIFACT_NAMES = { csv: "下载结果 CSV", per_image_csv: "下载逐图 CSV", report: "下载报告", zip: "打包下载 ZIP", metrics_json: "下载指标 JSON", json: "下载 JSON" };
const PAGE_NAMES = { detect: "图片检测", review: "病例复核", evaluate: "评估对比" };
const MAX_FILE = 10 * 1024 * 1024;
const MAX_TOTAL = 40 * 1024 * 1024;

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined && value !== null) node.textContent = String(value);
  return node;
}
function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  $("#global-error").textContent = message || "发生了未知错误，请重试。";
  $("#global-error").hidden = false;
  $("#global-success").hidden = true;
}
function clearMessages() { $("#global-error").hidden = true; $("#global-success").hidden = true; }
function showSuccess(message) { $("#global-success").textContent = message; $("#global-success").hidden = false; $("#global-error").hidden = true; }
function fmtNumber(value, digits = 0) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits }); }
function fmtRate(value) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(1)}%`; }
function fmtMap(value) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(3); }
function fmtDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
function fileUrl(runId, relative) {
  if (typeof relative !== "string" || !relative) return null;
  if (relative.startsWith("/files/")) return relative;
  if (/^https?:|^data:|^javascript:/i.test(relative)) return null;
  const parts = relative.replace(/^\/+/, "").split("/");
  if (parts.some(part => part === "." || part === ".." || part.includes("\\"))) return null;
  return `/files/${encodeURIComponent(runId)}/${parts.map(encodeURIComponent).join("/")}`;
}
async function api(path, body) {
  const options = { headers: { Accept: "application/json" }, cache: "no-store", credentials: "same-origin" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.headers["X-CSRF-Token"] = state.csrf;
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); } catch { throw new Error("无法连接本地服务。请确认工作台仍在运行，再刷新页面。"); }
  let data;
  try { data = await response.json(); } catch { throw new Error(`服务返回了无法读取的内容（${response.status}）。`); }
  if (!response.ok) {
    const detail = data.error?.message || data.error || data.message || `请求失败（${response.status}）`;
    throw new Error(typeof detail === "string" ? detail : "服务未能完成请求，请检查运行日志。");
  }
  return data;
}
function switchPage(page) {
  if (!PAGE_NAMES[page]) page = "detect";
  $$(".page").forEach(node => { node.hidden = node.id !== `page-${page}`; });
  $$(".nav-item").forEach(button => {
    const active = button.dataset.page === page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("#breadcrumb-page").textContent = PAGE_NAMES[page];
  if (window.location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
}
function updateButtons() {
  $("#predict-button").disabled = !state.ready || state.busy || !state.files.length;
  $("#demo-button").disabled = !state.ready || state.busy;
  $("#evaluate-button").disabled = !state.ready || state.busy;
  $("#file-input").disabled = state.busy;
  $$(".remove-file").forEach(button => { button.disabled = state.busy; });
  $("#review-save").disabled = state.reviewSaving || !state.reviewCase;
}
function refreshFileList() {
  const list = $("#file-list");
  list.replaceChildren();
  state.files.forEach((file, index) => {
    const row = element("li", "file-row");
    row.append(element("span", "", file.name), element("small", "", `${(file.size / 1024 / 1024).toFixed(1)} MB`));
    const remove = element("button", "remove-file", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `取消选择 ${file.name}`);
    remove.addEventListener("click", () => { state.files.splice(index, 1); refreshFileList(); });
    row.append(remove); list.append(row);
  });
  updateButtons();
}
function selectFiles(incoming) {
  if (state.busy) return;
  clearMessages();
  const next = state.files.slice();
  for (const file of incoming) {
    if (!/\.(jpe?g|png|webp)$/i.test(file.name) || (file.type && !["image/jpeg", "image/png", "image/webp"].includes(file.type))) { showError(`“${file.name}”的格式暂不支持，请选择 JPG、PNG 或 WebP。`); return; }
    if (file.size > MAX_FILE) { showError(`“${file.name}”超过 10 MB，请先压缩图片。`); return; }
    if (!file.size) { showError(`“${file.name}”是空文件，无法检测。`); return; }
    if (!next.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) next.push(file);
  }
  if (next.length > 12) { showError("一次最多检测 12 张图片，请分批上传。"); return; }
  if (next.reduce((sum, item) => sum + item.size, 0) > MAX_TOTAL) { showError("所选图片合计超过 40 MB，请减少图片数量或压缩后重试。"); return; }
  state.files = next;
  refreshFileList();
}
function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: String(reader.result).split(",")[1] });
    reader.onerror = () => reject(new Error(`无法读取“${file.name}”，请重新选择。`));
    reader.readAsDataURL(file);
  });
}
function progress(section, message, done, total) {
  const node = $(`#${section}-progress`);
  node.hidden = false;
  $(".progress-message", node).textContent = message || "请稍候，本地模型正在处理。";
  const bar = $("progress", node);
  if (Number(total) > 0) { bar.max = Number(total); bar.value = Math.min(Number(done) || 0, Number(total)); }
  else bar.removeAttribute("value");
}
async function executeJob(section, create) {
  if (state.busy || !state.ready) return;
  clearMessages(); state.busy = true; updateButtons();
  progress(section, "准备数据中…");
  try {
    const result = await create();
    if (!result.job_id) throw new Error("服务没有返回任务编号，请检查服务日志后重试。");
    let networkFailures = 0;
    while (true) {
      let job;
      try { job = await api(`/api/jobs/${encodeURIComponent(result.job_id)}`); networkFailures = 0; }
      catch (error) {
        networkFailures += 1;
        if (networkFailures >= 4) throw new Error(`${error.message} 本地任务可能仍在运行，可稍后在历史记录中查看。`);
        progress(section, "连接暂时中断，正在重新获取进度…");
        await new Promise(resolve => setTimeout(resolve, 1800)); continue;
      }
      progress(section, job.progress?.message, job.progress?.done, job.progress?.total);
      if (job.status === "failed") throw new Error(typeof job.error === "string" ? job.error : job.error?.message || "处理失败，请检查服务日志后重试。");
      if (job.status === "completed") {
        if (!job.run_id) throw new Error("任务已完成，但未返回记录编号。请刷新历史记录。");
        await refreshRuns();
        if (section === "detect") {
          await loadRun(job.run_id, "detect");
          $("#detect-results-title").scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
          const run = await api(`/api/runs/${encodeURIComponent(job.run_id)}`);
          renderEvaluation(run);
        }
        showSuccess(section === "detect" ? "检测已完成，结果已保存在本机。可前往“病例复核”记录你的判断。" : "评估已完成，参数、逐图结果与报告已保存在本机。");
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) { showError(error); }
  finally { state.busy = false; $(`#${section}-progress`).hidden = true; updateButtons(); }
}
async function refreshRuns() {
  const data = await api("/api/runs");
  state.runs = Array.isArray(data.runs) ? data.runs : [];
  populateRuns($("#detect-run-select"), state.runs.filter(run => run.kind !== "evaluate" && run.kind !== "evaluation"), "请选择历史检测");
  populateRuns($("#review-run-select"), state.runs, "请选择记录");
  renderHistory();
}
function runLabel(run) {
  const kind = ["evaluate", "evaluation"].includes(run.kind) ? `评估 ${run.settings?.split || ""}` : "检测";
  return `${fmtDate(run.created_at)} · ${kind} · ${run.case_count ?? run.cases?.length ?? "—"} 张`;
}
function populateRuns(select, runs, placeholder) {
  const previous = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  runs.filter(run => !run.status || run.status === "completed").forEach(run => { select.add(new Option(runLabel(run), String(run.id))); });
  if (Array.from(select.options).some(option => option.value === previous)) select.value = previous;
}
async function loadRun(id, target) {
  if (!id) return;
  const token = ++state.loadToken[target];
  try {
    const run = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (token !== state.loadToken[target]) return;
    if (target === "detect") {
      state.detectRun = run;
      $("#detect-run-select").value = String(run.id);
      renderDetection(run);
    } else {
      state.reviewRun = run;
      state.reviewCase = null;
      $("#review-run-select").value = String(run.id);
      renderReview();
    }
  } catch (error) { if (token === state.loadToken[target]) showError(error); }
}
function makeImage(runId, path, alt, className) {
  const src = fileUrl(runId, path);
  if (!src) return element("p", "image-error", "此图片暂不可用");
  const image = element("img", className);
  image.src = src; image.alt = alt; image.loading = "lazy"; image.decoding = "async";
  image.addEventListener("error", () => { image.replaceWith(element("p", "image-error", "图片暂不可用，请检查该记录的文件是否完整。")); }, { once: true });
  return image;
}
function imagePanel(run, item, field, title) {
  const panel = element("figure", "image-panel"); panel.append(element("figcaption", "", title));
  const path = fileUrl(run.id, item[field]);
  const wrapper = path ? element("a", "") : element("div", "");
  if (path) { wrapper.href = path; wrapper.target = "_blank"; wrapper.rel = "noopener"; wrapper.setAttribute("aria-label", `${title}，在新窗口查看完整图片`); }
  wrapper.append(makeImage(run.id, item[field], `${item.name} · ${title}`, field === "comparison" ? "comparison-image" : ""));
  panel.append(wrapper); return panel;
}
function caseStatus(item) {
  if (item.review?.status) return STATUS_NAMES[item.review.status] || "已复核";
  return { false_positive: "自动统计：误报", false_negative: "自动统计：漏检", both: "误报与漏检", none: "尚未人工复核" }[item.error_type] || "尚未人工复核";
}
function caseStrip(root, run, cases, selected, select) {
  root.replaceChildren();
  cases.forEach(item => {
    const button = element("button", `case-thumb${item.id === selected ? " active" : ""}`);
    button.type = "button"; button.setAttribute("aria-pressed", String(item.id === selected));
    button.setAttribute("aria-label", `${item.name}，${item.count ?? item.detections?.length ?? 0} 个候选框`);
    button.append(makeImage(run.id, item.original, item.name), element("span", "case-name", item.name), element("small", "", caseStatus(item)));
    button.addEventListener("click", () => select(item)); root.append(button);
  });
}
function renderViewer(root, run, item, review = false) {
  root.replaceChildren();
  const header = element("div", "viewer-header"); header.append(element("h3", "", item.name));
  const count = item.count ?? item.detections?.length ?? 0;
  header.append(element("span", `badge${count ? " caution" : ""}`, item.error ? "处理异常" : count ? `${count} 个候选病斑` : "未检出病斑")); root.append(header);
  if (item.error) { root.append(element("div", "notice error", typeof item.error === "string" ? item.error : "此图片处理失败，请查看报告。")); }
  const pair = element("div", "image-pair");
  pair.append(imagePanel(run, item, "original", "原始照片"), imagePanel(run, item, "annotated", "模型检测 · 点击放大")); root.append(pair);
  if (review && item.comparison) {
    const comparison = imagePanel(run, item, "comparison", "人工标签与模型预测对照 · 以图内图例为准");
    comparison.classList.add("comparison-panel"); root.append(comparison);
  }
  const meta = element("div", "viewer-meta");
  meta.append(element("span", "", `${fmtNumber(item.width)} × ${fmtNumber(item.height)} px`), element("span", "", `推理 ${fmtNumber(item.inference_ms, 1)} ms`), element("span", "", `尺寸 ${run.settings?.imgsz ?? "—"} / 阈值 ${run.settings?.conf ?? "—"}`));
  if (item.metrics) meta.append(element("span", "", `框级 TP ${fmtNumber(item.metrics.tp)} / FP ${fmtNumber(item.metrics.fp)} / FN ${fmtNumber(item.metrics.fn)}`));
  root.append(meta);
  if (count && item.detections?.length) {
    root.append(element("h4", "crop-heading", "病斑局部 · 分数代表模型置信度"));
    const crops = element("div", "crop-grid");
    item.detections.forEach((detection, index) => {
      const crop = element("figure", "crop-card");
      if (detection.crop) {
        const href = fileUrl(run.id, detection.crop);
        const link = element("a", "");
        if (href) { link.href = href; link.target = "_blank"; link.rel = "noopener"; link.setAttribute("aria-label", `放大查看候选病斑 ${index + 1}`); }
        link.append(makeImage(run.id, detection.crop, `候选病斑 ${index + 1}`)); crop.append(link);
      }
      else crop.append(element("p", "help", "本记录未保存局部图"));
      crop.append(element("figcaption", "", `#${index + 1} · 置信度 ${fmtMap(detection.confidence)}`)); crops.append(crop);
    }); root.append(crops);
  } else if (!item.error) root.append(element("p", "no-detections", "此阈值下未检出溃疡病斑。仍可能存在其他病害，或有模型未识别的病斑。"));
}
function downloadLink(run, path, label) {
  const href = fileUrl(run.id, path); if (!href) return null;
  const link = element("a", "button secondary small", `${label} ↓`); link.href = href; link.download = ""; return link;
}
function renderDownloads(root, run, item) {
  root.replaceChildren();
  if (item?.annotated) { const link = downloadLink(run, item.annotated, "保存预测图"); if (link) root.append(link); }
  Object.entries(run.artifacts || {}).forEach(([key, path]) => {
    if (!ARTIFACT_NAMES[key]) return;
    const link = downloadLink(run, path, ARTIFACT_NAMES[key]); if (link) root.append(link);
  });
}
function renderDetection(run) {
  const cases = Array.isArray(run.cases) ? run.cases : [];
  $("#detect-empty").hidden = cases.length > 0; $("#detect-results").hidden = cases.length === 0;
  if (!cases.length) return;
  const summary = $("#detect-summary"); summary.replaceChildren();
  const withCandidates = cases.filter(item => (item.count ?? item.detections?.length ?? 0) > 0).length;
  const counts = [[cases.length, " 张照片"], [withCandidates, " 张含候选病斑"], [cases.reduce((sum, item) => sum + (item.count ?? item.detections?.length ?? 0), 0), " 个候选框"]];
  counts.forEach(([value, label]) => { const piece = element("span", ""); piece.append(element("strong", "", value), document.createTextNode(label)); summary.append(piece); });
  function select(item) {
    state.detectCase = item;
    caseStrip($("#detect-cases"), run, cases, item.id, select);
    renderViewer($("#detect-viewer"), run, item);
    renderDownloads($("#detect-downloads"), run, item);
  }
  select(cases[0]);
}
function matchesFilter(item, filter) {
  if (filter === "all") return true;
  if (filter === "unreviewed") return !item.review?.status;
  if (item.review?.status) return item.review.status === filter;
  if (filter === "false_positive") return ["false_positive", "both"].includes(item.error_type);
  if (filter === "false_negative") return ["false_negative", "both"].includes(item.error_type);
  return false;
}
function renderReview() {
  const run = state.reviewRun;
  const all = run?.cases || [];
  const cases = all.filter(item => matchesFilter(item, $("#review-filter").value));
  $("#review-empty").hidden = cases.length > 0; $("#review-content").hidden = cases.length === 0;
  $("#review-count").textContent = run ? `${cases.length} / ${all.length} 张` : "";
  if (!cases.length) {
    $("#review-empty h3").textContent = run ? "当前筛选下没有病例" : "为下一次改进积累证据";
    $("#review-empty p").textContent = run ? "可切换筛选条件，或选择另一条历史记录。" : "先选择一条已有记录，再逐张复核。未标注的上传图片没有自动判定的对错。";
    state.reviewCase = null; updateButtons(); return;
  }
  function select(item) {
    state.reviewCase = item;
    caseStrip($("#review-cases"), run, cases, item.id, select);
    renderViewer($("#review-viewer"), run, item, true);
    $("#review-case-name").textContent = item.name;
    $("#review-form").reset();
    const radio = $$("input[name=review-status]").find(input => input.value === item.review?.status);
    if (radio) radio.checked = true;
    $("#review-reason").value = item.review?.reason || "";
    $("#review-note").value = item.review?.note || "";
    updateButtons();
  }
  select(cases.find(item => item.id === state.reviewCase?.id) || cases[0]);
}
async function saveReview(event) {
  event.preventDefault();
  if (state.reviewSaving || !state.reviewCase || !state.reviewRun) return;
  const selected = $("input[name=review-status]:checked"); if (!selected) return;
  const run = state.reviewRun, item = state.reviewCase;
  const body = { run_id: run.id, case_id: item.id, status: selected.value, reason: $("#review-reason").value.trim(), note: $("#review-note").value.trim() };
  state.reviewSaving = true; updateButtons(); clearMessages(); $("#review-save").textContent = "正在保存…";
  try {
    const data = await api("/api/review", body); item.review = data.review || body;
    showSuccess(`已保存“${item.name}”的复核记录，原始数据标签保持不变。`);
    if (state.reviewRun === run) renderReview();
  } catch (error) { showError(error); }
  finally { state.reviewSaving = false; $("#review-save").textContent = "保存复核记录"; updateButtons(); }
}
function metricCard(label, value, detail, minor = false) {
  const card = element("div", `metric${minor ? " minor" : ""}`);
  card.append(element("span", "", label), element("strong", "", value), element("small", "", detail)); return card;
}
function renderEvaluation(run) {
  const root = $("#eval-metrics"); root.replaceChildren(); root.hidden = false;
  const metrics = run.metrics || {}, lesion = metrics.lesion || {}, image = metrics.image || {}, timing = metrics.timing || {};
  const card = element("section", "card metrics-card");
  const head = element("div", "section-head"); head.append(element("h2", "", "本次评估结果"), element("span", `badge${run.settings?.split === "test" ? " caution" : ""}`, run.settings?.split === "test" ? "test · 阶段性诊断" : `${run.settings?.split || "val"} · 验证集`)); card.append(head);
  card.append(element("p", "run-detail-summary", `${fmtDate(run.created_at)} · ${run.case_count ?? run.cases?.length ?? "—"} 张 · 图像尺寸 ${run.settings?.imgsz ?? "—"} · 置信度 ${run.settings?.conf ?? "—"}`));
  const grid = element("div", "metric-grid");
  grid.append(metricCard("病斑精确率 Precision", fmtRate(lesion.precision), "预测框中正确框所占的比例"), metricCard("病斑召回率 Recall", fmtRate(lesion.recall), "真实病斑中被检出的比例"), metricCard("病斑 F1", fmtRate(lesion.f1), "精确率与召回率的综合指标"), metricCard("阴性图片误报率", fmtRate(image.false_positive_rate), "无溃疡图中出现候选框的比例"));
  grid.append(metricCard("正确框 / 误报 / 漏检", `${fmtNumber(lesion.tp)} / ${fmtNumber(lesion.fp)} / ${fmtNumber(lesion.fn)}`, "TP / FP / FN · 病斑级", true), metricCard("患病图片检出率", fmtRate(image.sensitivity), `阳性 ${fmtNumber(image.positive_count)} 张 · 图片级`, true), metricCard("阴性图片正确排除率", fmtRate(image.specificity), `阴性 ${fmtNumber(image.negative_count)} 张 · 图片级`, true), metricCard("平均推理耗时", `${fmtNumber(timing.mean_inference_ms, 1)} ms`, "仅推理，不含上传和报告生成", true)); card.append(grid);
  card.append(element("p", "help", "以上是固定置信度阈值下的结果；病斑是否匹配按评估的 IoU 口径判断。“—”表示该指标不适用或未提供。"));
  card.append(element("h3", "metric-divider", "mAP · 跨阈值检测指标"));
  if (metrics.map && (metrics.map.map50 !== undefined || metrics.map.map50_95 !== undefined)) {
    const mapGrid = element("div", "metric-grid map");
    mapGrid.append(metricCard("mAP50", fmtMap(metrics.map.map50), "IoU = 0.50"), metricCard("mAP50–95", fmtMap(metrics.map.map50_95), "IoU = 0.50 至 0.95")); card.append(mapGrid);
  } else card.append(element("p", "help", "本次未计算 mAP。上方的固定阈值结果不能替代 mAP。"));
  if (run.settings?.split === "test") card.append(element("p", "notice warning", "当前 test 曾用于错误分析和标注调整，只能作为阶段性诊断集。这里的指标不代表独立外部测试表现。"));
  const downloads = element("div", "download-row"); renderDownloads(downloads, run); card.append(downloads);
  const review = element("button", "button secondary small", "查看逐图结果与错误病例 →"); review.type = "button";
  review.addEventListener("click", () => { $("#review-filter").value = "all"; switchPage("review"); loadRun(run.id, "review"); }); card.append(review);
  root.append(card);
}
function renderHistory() {
  const root = $("#evaluation-history");
  const runs = state.runs.filter(run => ["evaluate", "evaluation"].includes(run.kind) || run.settings?.split);
  root.replaceChildren();
  if (!runs.length) { const empty = element("div", "empty-state compact"); empty.append(element("h3", "", "还没有评估记录"), element("p", "", "运行后会显示固定阈值结果与可选的 mAP。")); root.append(empty); return; }
  const table = element("table", ""); const caption = element("caption", "sr-only", "历史评估记录，固定阈值指标与 mAP 分别显示"); table.append(caption);
  const head = element("thead", ""), headRow = element("tr", "");
  ["时间 / 数据集", "尺寸 / 阈值", "精确率", "召回率", "阴性误报率", "mAP50", "mAP50–95", ""].forEach(text => { const cell = element("th", "", text); cell.scope = "col"; headRow.append(cell); }); head.append(headRow); table.append(head);
  const body = element("tbody", "");
  runs.forEach(run => {
    const metrics = run.metrics || {}, row = element("tr", "");
    [ `${fmtDate(run.created_at)} / ${run.settings?.split === "test" ? "test（诊断）" : run.settings?.split || "—"}`, `${run.settings?.imgsz ?? "—"} / ${run.settings?.conf ?? "—"}`, fmtRate(metrics.lesion?.precision), fmtRate(metrics.lesion?.recall), fmtRate(metrics.image?.false_positive_rate), fmtMap(metrics.map?.map50), fmtMap(metrics.map?.map50_95) ].forEach(value => row.append(element("td", "", value)));
    const action = element("td", ""), button = element("button", "table-link", "查看 →"); button.type = "button";
    button.addEventListener("click", async () => { button.disabled = true; try { renderEvaluation(await api(`/api/runs/${encodeURIComponent(run.id)}`)); $("#eval-metrics").scrollIntoView({ behavior: "smooth", block: "start" }); } catch (error) { showError(error); } finally { button.disabled = false; } });
    action.append(button); row.append(action); body.append(row);
  }); table.append(body); root.append(table);
}
function updateSplitNote() {
  const diagnostic = $("#eval-split").value === "test", note = $("#eval-split-note");
  note.classList.toggle("diagnostic", diagnostic);
  note.textContent = diagnostic ? "当前 test 已参与错误分析和标注调整，只用于阶段性诊断。不要据此挑选阈值，再把结果称为独立测试成绩。" : "val 用于开发与比较。默认演示参数来自既有诊断结果，不能替代独立外部测试。";
}
async function initialize() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => switchPage(button.dataset.page)));
  window.addEventListener("hashchange", () => switchPage(location.hash.slice(1)));
  switchPage(location.hash.slice(1) || "detect");
  $("#file-input").addEventListener("change", event => { selectFiles(Array.from(event.target.files)); event.target.value = ""; });
  const drop = $("#drop-zone");
  ["dragenter", "dragover"].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); if (!state.busy) drop.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(type => drop.addEventListener(type, event => { event.preventDefault(); drop.classList.remove("dragging"); }));
  drop.addEventListener("drop", event => selectFiles(Array.from(event.dataTransfer.files)));
  $("#predict-conf").addEventListener("input", event => { $("#predict-conf-value").textContent = Number(event.target.value).toFixed(2); });
  $("#predict-button").addEventListener("click", () => executeJob("detect", async () => api("/api/predict", { files: await Promise.all(state.files.map(readBase64)), settings: { imgsz: Number($("#predict-imgsz").value), conf: Number($("#predict-conf").value) } })));
  $("#demo-button").addEventListener("click", () => executeJob("detect", () => api("/api/demo", {})));
  $("#detect-run-select").addEventListener("change", event => loadRun(event.target.value, "detect"));
  $("#review-run-select").addEventListener("change", event => loadRun(event.target.value, "review"));
  $("#review-filter").addEventListener("change", renderReview);
  $("#review-form").addEventListener("submit", saveReview);
  $("#eval-split").addEventListener("change", updateSplitNote);
  $("#evaluate-button").addEventListener("click", () => {
    const input = $("#eval-conf"); if (!input.reportValidity() || input.value === "") { if (input.value === "") showError("请输入评估置信度阈值。"); return; }
    const settings = { split: $("#eval-split").value, imgsz: Number($("#eval-imgsz").value), conf: Number(input.value), include_map: $("#eval-map").checked };
    executeJob("evaluate", () => api("/api/evaluate", { settings }));
  });
  $("#refresh-history").addEventListener("click", async event => { const button = event.currentTarget; button.disabled = true; try { await refreshRuns(); } catch (error) { showError(error); } finally { button.disabled = false; } });
  try {
    const data = await api("/api/status"); state.csrf = data.csrf_token || ""; state.ready = Boolean(data.model_ready && state.csrf);
    $("#model-state").textContent = data.model_ready ? "已就绪" : "权重未就绪";
    $("#side-status").textContent = data.model_ready ? "本地模型已就绪" : "请检查模型权重";
    $("#side-dot").classList.toggle("failed", !data.model_ready);
    if (data.defaults?.imgsz && [640, 960].includes(Number(data.defaults.imgsz))) { $("#predict-imgsz").value = String(data.defaults.imgsz); $("#eval-imgsz").value = String(data.defaults.imgsz); }
    if (data.defaults?.conf !== undefined) { $("#predict-conf").value = String(data.defaults.conf); $("#predict-conf-value").textContent = Number(data.defaults.conf).toFixed(2); $("#eval-conf").value = String(data.defaults.conf); }
    if (data.counts) $("#dataset-counts").textContent = `train ${fmtNumber(data.counts.train)} · val ${fmtNumber(data.counts.val)} · test ${fmtNumber(data.counts.test)}`;
    else $("#dataset-counts").textContent = "数据集数量暂不可用";
    const warnings = Array.isArray(data.warnings) ? data.warnings : data.warnings ? [data.warnings] : [];
    if (warnings.length) { $("#global-warnings").textContent = warnings.map(message => String(message).replace(/[。；;]+$/, "")).join("；") + "。"; $("#global-warnings").hidden = false; }
    if (!state.ready) showError(data.model_ready ? "服务未返回安全令牌，请刷新页面后重试。" : "本地模型尚未就绪。请检查配置中的权重路径，历史记录仍可查看。");
    await refreshRuns();
  } catch (error) { $("#side-status").textContent = "本地服务未连接"; $("#side-dot").classList.add("failed"); $("#model-state").textContent = "未连接"; showError(error); }
  finally { updateButtons(); }
}
initialize();
