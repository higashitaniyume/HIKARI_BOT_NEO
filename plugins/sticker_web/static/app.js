const state = {
  packs: [],
  keywords: [],
  totalStickers: 0,
};
const MAX_UPLOAD_FILES = 99;

const $ = (selector) => document.querySelector(selector);

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  toast.classList.toggle("error", isError);
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

async function readJsonResponse(res, fallbackMessage) {
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("请先登录。");
  }

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || fallbackMessage);
  }
  return data;
}

function option(value, text) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = text;
  return node;
}

async function fetchState() {
  const res = await fetch("/api/state", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取贴纸数据失败");
  state.packs = data.packs || [];
  state.keywords = data.keywords || [];
  state.totalStickers = Number(data.total_stickers || 0);
  render();
}

function renderSelects() {
  const uploadSelect = $("#existing_pack");
  const keywordSelect = $("#keywordPack");
  uploadSelect.replaceChildren(option("", "新建贴纸包"));
  keywordSelect.replaceChildren();

  for (const pack of state.packs) {
    uploadSelect.append(option(pack.name, `${pack.name} (${pack.count} 个)`));
    keywordSelect.append(option(pack.name, `${pack.name} (${pack.count} 个)`));
  }

  if (!state.packs.length) {
    keywordSelect.append(option("", "暂无贴纸包"));
  }
}

function chip(keyword, packName) {
  const node = document.createElement("span");
  node.className = "chip";
  const label = document.createElement("span");
  label.textContent = String(keyword ?? "");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "×";
  remove.title = `删除 ${packName} 的关键词 ${keyword}`;
  remove.addEventListener("click", () => deleteKeyword(packName, keyword));
  node.append(label, remove);
  return node;
}

function previewUrl(stickerId) {
  return `/api/stickers/${encodeURIComponent(stickerId)}`;
}

function renderPreviewStrip(pack) {
  const strip = document.createElement("div");
  strip.className = "preview-strip";
  const previews = Array.isArray(pack.previews) ? pack.previews : [];

  if (!previews.length) {
    strip.classList.add("empty-preview");
    strip.textContent = "暂无预览";
    return strip;
  }

  for (const stickerId of previews) {
    const frame = document.createElement("div");
    frame.className = "preview-frame";
    const image = document.createElement("img");
    image.src = previewUrl(stickerId);
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    frame.append(image);
    strip.append(frame);
  }

  return strip;
}

function renderPacks() {
  const list = $("#packList");
  list.className = "pack-list";
  list.replaceChildren();

  if (!state.packs.length) {
    list.className = "pack-list empty";
    list.textContent = "暂无贴纸包";
    return;
  }

  for (const pack of state.packs) {
    const item = document.createElement("article");
    item.className = "pack-card";

    const head = document.createElement("div");
    head.className = "pack-head";
    const title = document.createElement("div");
    title.className = "pack-title";
    title.textContent = pack.name;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${pack.count} 张`;
    head.append(title, badge);

    const chips = document.createElement("div");
    chips.className = "chips";
    if (pack.keywords?.length) {
      for (const keyword of pack.keywords) {
        chips.append(chip(keyword, pack.name));
      }
    } else {
      const empty = document.createElement("span");
      empty.className = "badge";
      empty.textContent = "暂无关键词";
      chips.append(empty);
    }

    item.append(renderPreviewStrip(pack), head, chips);
    list.append(item);
  }
}

function renderKeywords() {
  const list = $("#keywordList");
  list.className = "list";
  list.replaceChildren();

  if (!state.keywords.length) {
    list.className = "list empty";
    list.textContent = "暂无关键词";
    return;
  }

  for (const relation of state.keywords) {
    const item = document.createElement("article");
    item.className = "keyword-card";

    const head = document.createElement("div");
    head.className = "item-head";
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = relation.keyword;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${relation.packs.length} 个包`;
    head.append(title, badge);

    const chips = document.createElement("div");
    chips.className = "chips";
    for (const packName of relation.packs) {
      const packChip = document.createElement("span");
      packChip.className = "chip";
      packChip.textContent = packName;
      chips.append(packChip);
    }

    item.append(head, chips);
    list.append(item);
  }
}

function render() {
  $("#stickerCount").textContent = state.totalStickers;
  $("#packCount").textContent = state.packs.length;
  $("#keywordCount").textContent = state.keywords.length;
  renderSelects();
  renderPacks();
  renderKeywords();
}

function updateFileHint() {
  const input = $("#file");
  const hint = $("#fileHint");
  const count = input.files?.length || 0;
  if (!count) {
    hint.textContent = `可多选，单次最多 ${MAX_UPLOAD_FILES} 个文件。`;
    return;
  }

  if (count > MAX_UPLOAD_FILES) {
    input.value = "";
    hint.textContent = `已超过上限，请重新选择 ${MAX_UPLOAD_FILES} 个以内的文件。`;
    showToast(`一次最多上传 ${MAX_UPLOAD_FILES} 个文件。`, true);
    return;
  }

  hint.textContent = `已选择 ${count} 个文件。`;
}

function setJobProgress(job, prefix = "upload") {
  const panel = $(`#${prefix}Progress`);
  const text = $(`#${prefix}ProgressText`);
  const count = $(`#${prefix}ProgressCount`);
  const bar = $(`#${prefix}ProgressBar`);
  const detail = $(`#${prefix}ProgressDetail`);
  const total = Number(job.total || 0);
  const processed = Number(job.processed || 0);
  const failed = Array.isArray(job.failed) ? job.failed : [];
  const percent = total > 0 ? Math.round((processed / total) * 100) : 0;

  panel.hidden = false;
  text.textContent = job.message || "正在处理...";
  count.textContent = `${processed}/${total}`;
  bar.value = percent;
  detail.textContent = [
    job.current ? `当前：${job.current}` : "",
    `新增 ${job.saved || 0} 个`,
    `复用 ${job.reused || 0} 个`,
    `失败 ${failed.length} 个`,
    failed.length ? `失败详情：${failed.slice(0, 5).join("；")}${failed.length > 5 ? `；另有 ${failed.length - 5} 个失败项已省略` : ""}` : "",
  ].filter(Boolean).join("，");
}

function setUploadProgress(job) {
  setJobProgress(job, "upload");
}

async function pollUploadJob(jobId, prefix = "upload", completeMessage = "处理完成。") {
  while (true) {
    const res = await fetch(`/api/uploads/${jobId}`, { cache: "no-store" });
    const job = await readJsonResponse(res, "读取上传进度失败");
    setJobProgress(job, prefix);

    if (job.status === "done" || job.status === "failed") {
      await fetchState();
      showToast(job.message || completeMessage, job.status === "failed");
      return;
    }

    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
}

async function importTelegramStickers(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const url = $("#tgUrl").value.trim();
  if (!url) {
    showToast("请输入 Telegram 贴纸包链接。", true);
    return;
  }

  const submitButton = form.querySelector("button[type='submit']");
  submitButton.disabled = true;
  setJobProgress({
    status: "queued",
    total: 0,
    processed: 0,
    saved: 0,
    reused: 0,
    failed: [],
    message: "正在创建 Telegram 导入任务...",
  }, "tg");

  try {
    const res = await fetch("/api/tg-stickers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        pack: $("#tgPack").value.trim(),
        keyword: $("#tgKeyword").value.trim(),
        refresh: $("#tgRefresh").checked,
      }),
    });
    const job = await readJsonResponse(res, "创建 Telegram 导入任务失败");
    setJobProgress(job, "tg");
    await pollUploadJob(job.id, "tg", "Telegram 贴纸包导入完成。");
    form.reset();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    submitButton.disabled = false;
  }
}

async function uploadStickers(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fileCount = $("#file").files?.length || 0;
  if (fileCount <= 0) {
    showToast("请选择要上传的文件。", true);
    return;
  }
  if (fileCount > MAX_UPLOAD_FILES) {
    showToast(`一次最多上传 ${MAX_UPLOAD_FILES} 个文件。`, true);
    return;
  }

  const submitButton = form.querySelector("button[type='submit']");
  submitButton.disabled = true;
  setUploadProgress({
    status: "uploading",
    total: fileCount,
    processed: 0,
    saved: 0,
    reused: 0,
    failed: [],
    message: "正在上传文件...",
  });

  try {
    const res = await fetch("/api/uploads", {
      method: "POST",
      body: new FormData(form),
    });
    const job = await readJsonResponse(res, "创建上传任务失败");
    setUploadProgress(job);
    await pollUploadJob(job.id);
    form.reset();
    updateFileHint();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    submitButton.disabled = false;
  }
}

async function addKeyword(event) {
  event.preventDefault();
  const pack = $("#keywordPack").value;
  const keyword = $("#keywordInput").value.trim();
  if (!pack || !keyword) {
    showToast("请选择贴纸包并填写一个或多个关键词。", true);
    return;
  }

  try {
    const res = await fetch("/api/keywords", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack, keyword }),
    });
    const data = await readJsonResponse(res, "添加失败");
    state.packs = data.packs || [];
    state.keywords = data.keywords || [];
    state.totalStickers = Number(data.total_stickers || 0);
    $("#keywordInput").value = "";
    render();
    showToast("关键词关联已添加。");
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteKeyword(pack, keyword) {
  try {
    const params = new URLSearchParams({ pack, keyword });
    const res = await fetch(`/api/keywords?${params}`, { method: "DELETE" });
    const data = await readJsonResponse(res, "删除失败");
    state.packs = data.packs || [];
    state.keywords = data.keywords || [];
    state.totalStickers = Number(data.total_stickers || 0);
    render();
    showToast("关键词关联已删除。");
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#keywordForm").addEventListener("submit", addKeyword);
$("#uploadForm").addEventListener("submit", uploadStickers);
$("#tgForm").addEventListener("submit", importTelegramStickers);
$("#refreshBtn").addEventListener("click", () => fetchState().then(() => showToast("已刷新。")).catch((err) => showToast(err.message, true)));
$("#file").addEventListener("change", updateFileHint);

fetchState().catch((err) => showToast(err.message, true));
