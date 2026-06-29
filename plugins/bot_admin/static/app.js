const state = {
  packs: [],
  keywords: [],
  selectedPackName: "",
  selectedPackDetail: null,
  inbox: [],
  voices: [],
  voiceKeywords: [],
  configFiles: [],
  selectedConfig: "",
  logFiles: [],
  selectedLog: "",
  totalStickers: 0,
  totalVoices: 0,
  totalVoiceKeywords: 0,
  ttsConfig: {},
  aiagentConfig: {},
  aiagentPersonas: [],
  accessRules: [],
  selectedAccessPlugin: "",
  editingTtsVoiceName: "",
  draggingInboxIds: [],
  pickerInboxIds: [],
};
const MAX_UPLOAD_FILES = 99;
const MAX_VOICE_UPLOAD_FILES = 20;
const RECENT_PACKS_KEY = "hikariStickerRecentPacks";
const RECENT_PACKS_LIMIT = 6;
const SIDEBAR_COLLAPSED_KEY = "hikariAdminSidebarCollapsed";
const VIEW_TITLES = {
  overview: "总览",
  stickers: "贴纸",
  inbox: "待整理",
  voices: "语音",
  settings: "设置",
  aiagent: "AI Agent",
  access: "权限",
  configs: "配置",
  logs: "日志",
};

const $ = (selector) => document.querySelector(selector);

function setView(view) {
  const target = VIEW_TITLES[view] ? view : "overview";
  for (const panel of document.querySelectorAll("[data-view]")) {
    const active = panel.dataset.view === target;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  }
  for (const button of document.querySelectorAll("[data-view-target]")) {
    button.classList.toggle("is-active", button.dataset.viewTarget === target);
  }
  $("#viewTitle").textContent = VIEW_TITLES[target];
  if (window.location.hash !== `#${target}`) {
    window.history.replaceState(null, "", `#${target}`);
  }
  if (target === "configs" && !state.configFiles.length) {
    fetchConfigFiles().catch((err) => showToast(err.message, true));
  }
  if (target === "logs" && !state.logFiles.length) {
    fetchLogFiles().catch((err) => showToast(err.message, true));
  }
  if (target === "aiagent" && !Object.keys(state.aiagentConfig || {}).length) {
    fetchAiAgentConfig().catch((err) => showToast(err.message, true));
  }
  if (target === "access" && !state.accessRules.length) {
    fetchAccessRules().catch((err) => showToast(err.message, true));
  }
}

function initNavigation() {
  for (const button of document.querySelectorAll("[data-view-target]")) {
    button.addEventListener("click", () => setView(button.dataset.viewTarget));
  }
  const initial = window.location.hash.replace(/^#/, "");
  setView(initial || "overview");
}

function setSidebarCollapsed(collapsed) {
  const shell = document.querySelector(".admin-shell");
  shell.classList.toggle("sidebar-collapsed", collapsed);
  const button = $("#sidebarToggle");
  button.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
  button.setAttribute("title", collapsed ? "展开侧边栏" : "收起侧边栏");
  button.setAttribute("aria-expanded", String(!collapsed));
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
  } catch {
    // 侧栏状态不影响后台的核心功能。
  }
}

function initSidebar() {
  let collapsed = false;
  try {
    collapsed = window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    // 浏览器禁用本地存储时使用展开状态。
  }
  setSidebarCollapsed(collapsed);
  $("#sidebarToggle").addEventListener("click", () => {
    setSidebarCollapsed(!document.querySelector(".admin-shell").classList.contains("sidebar-collapsed"));
  });
}

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

function packLabel(pack) {
  return `${pack.name} (${pack.count} 个)`;
}

function loadRecentPackNames() {
  try {
    const value = JSON.parse(window.localStorage.getItem(RECENT_PACKS_KEY) || "[]");
    return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function saveRecentPackName(packName) {
  const next = [
    packName,
    ...loadRecentPackNames().filter((name) => name !== packName),
  ].slice(0, RECENT_PACKS_LIMIT);
  try {
    window.localStorage.setItem(RECENT_PACKS_KEY, JSON.stringify(next));
  } catch {
    // 最近使用只是提效信息，浏览器禁用本地存储时不影响移动贴纸。
  }
}

async function fetchState() {
  const res = await fetch("/api/state", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取贴纸数据失败");
  state.packs = data.packs || [];
  state.keywords = data.keywords || [];
  state.totalStickers = Number(data.total_stickers || 0);
  await fetchVoiceState(false);
  await fetchTtsConfig(false);
  await fetchAiAgentConfig(false);
  await fetchInbox(false);
  if (state.selectedPackName && state.packs.some((pack) => pack.name === state.selectedPackName)) {
    await fetchPackDetail(state.selectedPackName, false);
  } else if (state.selectedPackName) {
    state.selectedPackName = "";
    state.selectedPackDetail = null;
  }
  render();
}

async function fetchVoiceState(shouldRender = true) {
  const res = await fetch("/api/voice-state", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取语音数据失败");
  state.voices = data.voices || [];
  state.voiceKeywords = data.keywords || [];
  state.totalVoices = Number(data.total_voices || 0);
  state.totalVoiceKeywords = Number(data.total_keywords || 0);
  if (shouldRender) {
    render();
  }
}

async function fetchInbox(shouldRender = true) {
  const res = await fetch("/api/inbox", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取待整理表情失败");
  state.inbox = data.items || [];
  if (shouldRender) {
    render();
  }
}

async function fetchPackDetail(packName, shouldRender = true) {
  const safeName = String(packName || "").trim();
  if (!safeName) {
    state.selectedPackName = "";
    state.selectedPackDetail = null;
    if (shouldRender) renderPackDetail();
    return;
  }
  const res = await fetch(`/api/packs/${encodeURIComponent(safeName)}`, { cache: "no-store" });
  const data = await readJsonResponse(res, "读取贴纸包详情失败");
  state.selectedPackName = data.pack?.name || safeName;
  state.selectedPackDetail = data.pack || null;
  if (shouldRender) {
    renderPackDetail();
  }
}

async function fetchTtsConfig(shouldRender = true) {
  const res = await fetch("/api/tts-config", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取 TTS 设置失败");
  state.ttsConfig = data.config || {};
  if (shouldRender) {
    render();
  }
}

async function fetchAiAgentConfig(shouldRender = true) {
  const res = await fetch("/api/aiagent-config", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取 AI Agent 设置失败");
  state.aiagentConfig = data.config || {};
  state.aiagentPersonas = data.personas || [];
  if (shouldRender) {
    renderAiAgentConfig();
  }
}

async function fetchAccessRules(shouldRender = true) {
  const res = await fetch("/api/access-rules", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取权限规则失败");
  state.accessRules = data.plugins || [];
  if (!state.selectedAccessPlugin && state.accessRules.length) {
    state.selectedAccessPlugin = state.accessRules[0].name;
  }
  if (shouldRender) {
    renderAccessRules();
  }
}

async function fetchConfigFiles() {
  const res = await fetch("/api/configs", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取配置列表失败");
  state.configFiles = data.files || [];
  renderConfigFiles();
}

async function openConfigFile(name) {
  state.selectedConfig = name;
  renderConfigFiles();
  const res = await fetch(`/api/configs/${encodeURIComponent(name)}`, { cache: "no-store" });
  const data = await readJsonResponse(res, "读取配置文件失败");
  $("#configEditorTitle").textContent = data.file?.name || name;
  $("#configEditorMeta").textContent = `${formatBytes(data.file?.size || 0)} / ${formatTime(data.file?.mtime || 0)}`;
  $("#configEditor").value = data.content || "";
}

function renderConfigFiles() {
  const list = $("#configFileList");
  list.replaceChildren();
  if (!state.configFiles.length) {
    list.className = "ops-list empty";
    list.textContent = "暂无配置文件";
    return;
  }
  list.className = "ops-list";
  for (const file of state.configFiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ops-list-item";
    button.classList.toggle("is-active", file.name === state.selectedConfig);
    button.addEventListener("click", () => openConfigFile(file.name).catch((err) => showToast(err.message, true)));

    const title = document.createElement("span");
    title.className = "ops-list-title";
    title.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "ops-list-meta";
    meta.textContent = `${formatBytes(file.size)} / ${formatTime(file.mtime)}`;
    button.append(title, meta);
    list.append(button);
  }
}

function currentAccessRule() {
  return state.accessRules.find((item) => item.name === state.selectedAccessPlugin) || null;
}

function joinIds(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function splitIds(value) {
  const seen = new Set();
  return String(value || "")
    .split(/[\s,，;；]+/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) return false;
      seen.add(item);
      return true;
    });
}

function renderAccessRules() {
  const list = $("#accessPluginList");
  list.replaceChildren();
  if (!state.accessRules.length) {
    list.className = "ops-list empty";
    list.textContent = "暂无可管理插件";
    $("#accessRulesForm").hidden = true;
    $("#accessEditorTitle").textContent = "选择一个插件";
    $("#accessEditorMeta").textContent = "未找到可管理的插件配置文件";
    return;
  }

  list.className = "ops-list";
  for (const plugin of state.accessRules) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ops-list-item";
    button.classList.toggle("is-active", plugin.name === state.selectedAccessPlugin);
    button.addEventListener("click", () => {
      state.selectedAccessPlugin = plugin.name;
      renderAccessRules();
    });

    const title = document.createElement("span");
    title.className = "ops-list-title";
    title.textContent = plugin.label || plugin.name;
    const meta = document.createElement("span");
    meta.className = "ops-list-meta";
    meta.textContent = plugin.name;
    button.append(title, meta);
    list.append(button);
  }

  const selected = currentAccessRule() || state.accessRules[0];
  state.selectedAccessPlugin = selected.name;
  const permissions = selected.permissions || {};
  const whitelist = permissions.whitelist || {};
  const blacklist = permissions.blacklist || {};
  $("#accessRulesForm").hidden = false;
  $("#accessEditorTitle").textContent = selected.label || selected.name;
  $("#accessEditorMeta").textContent = `${selected.name} / ${formatTime(selected.mtime || 0)}`;
  $("#accessWhitelistEnabled").checked = whitelist.enable === true;
  $("#accessBlacklistEnabled").checked = blacklist.enable === true;
  $("#accessWhitelistUsers").value = joinIds(whitelist.user);
  $("#accessWhitelistGroups").value = joinIds(whitelist.group);
  $("#accessBlacklistUsers").value = joinIds(blacklist.user);
  $("#accessBlacklistGroups").value = joinIds(blacklist.group);
}

function buildAccessPayload() {
  return {
    plugin: state.selectedAccessPlugin,
    permissions: {
      whitelist: {
        enable: $("#accessWhitelistEnabled").checked,
        user: splitIds($("#accessWhitelistUsers").value),
        group: splitIds($("#accessWhitelistGroups").value),
      },
      blacklist: {
        enable: $("#accessBlacklistEnabled").checked,
        user: splitIds($("#accessBlacklistUsers").value),
        group: splitIds($("#accessBlacklistGroups").value),
      },
    },
  };
}

async function saveAccessRules(event) {
  event.preventDefault();
  if (!state.selectedAccessPlugin) {
    showToast("请先选择一个插件。", true);
    return;
  }
  const button = $("#accessSaveBtn");
  button.disabled = true;
  try {
    const res = await fetch("/api/access-rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildAccessPayload()),
    });
    const data = await readJsonResponse(res, "保存权限规则失败");
    state.accessRules = data.plugins || [];
    renderAccessRules();
    showToast(data.message || "权限规则已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function saveCurrentConfig() {
  if (!state.selectedConfig) {
    showToast("请先选择一个配置文件。", true);
    return;
  }
  let normalized = "";
  try {
    normalized = JSON.stringify(JSON.parse($("#configEditor").value), null, 2);
  } catch (err) {
    showToast(`JSON 格式错误：${err.message}`, true);
    return;
  }

  const button = $("#configSaveBtn");
  button.disabled = true;
  try {
    const res = await fetch(`/api/configs/${encodeURIComponent(state.selectedConfig)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: normalized }),
    });
    const data = await readJsonResponse(res, "保存配置失败");
    $("#configEditor").value = data.config?.content || normalized;
    $("#configEditorMeta").textContent = `${formatBytes(data.config?.file?.size || 0)} / ${formatTime(data.config?.file?.mtime || 0)}`;
    await fetchConfigFiles();
    showToast(data.message || "配置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function fetchLogFiles() {
  const res = await fetch("/api/logs", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取日志列表失败");
  state.logFiles = data.files || [];
  renderLogFiles();
}

async function openLogFile(name) {
  state.selectedLog = name;
  renderLogFiles();
  const res = await fetch(`/api/logs/${encodeURIComponent(name)}?max_bytes=262144`, { cache: "no-store" });
  const data = await readJsonResponse(res, "读取日志失败");
  $("#logViewerTitle").textContent = data.file?.name || name;
  $("#logViewerMeta").textContent = `${formatBytes(data.file?.size || 0)} / ${formatTime(data.file?.mtime || 0)}${data.truncated ? " / 仅显示尾部" : ""}`;
  $("#logViewer").textContent = data.content || "";
}

function renderLogFiles() {
  const list = $("#logFileList");
  list.replaceChildren();
  if (!state.logFiles.length) {
    list.className = "ops-list empty";
    list.textContent = "暂无日志文件";
    return;
  }
  list.className = "ops-list";
  for (const file of state.logFiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ops-list-item";
    button.classList.toggle("is-active", file.name === state.selectedLog);
    button.addEventListener("click", () => openLogFile(file.name).catch((err) => showToast(err.message, true)));

    const title = document.createElement("span");
    title.className = "ops-list-title";
    title.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "ops-list-meta";
    meta.textContent = `${formatBytes(file.size)} / ${formatTime(file.mtime)}`;
    button.append(title, meta);
    list.append(button);
  }
}

async function reloadSelectedLog() {
  await fetchLogFiles();
  if (state.selectedLog) {
    await openLogFile(state.selectedLog);
  }
}

function renderSelects() {
  const uploadSelect = $("#existing_pack");
  const keywordSelect = $("#keywordPack");
  const inboxSelect = $("#inboxPack");
  const packMoveSelect = $("#packStickerMoveTarget");
  const voiceKeywordSelect = $("#voiceKeywordVoice");
  uploadSelect.replaceChildren(option("", "新建贴纸包"));
  keywordSelect.replaceChildren();
  inboxSelect.replaceChildren(option("", "选择已有贴纸包"));
  packMoveSelect.replaceChildren(option("", "选择目标贴纸包"));
  voiceKeywordSelect.replaceChildren();

  for (const pack of state.packs) {
    uploadSelect.append(option(pack.name, packLabel(pack)));
    keywordSelect.append(option(pack.name, packLabel(pack)));
    inboxSelect.append(option(pack.name, packLabel(pack)));
    if (pack.name !== state.selectedPackName) {
      packMoveSelect.append(option(pack.name, packLabel(pack)));
    }
  }

  for (const voice of state.voices) {
    voiceKeywordSelect.append(option(voice.id, `${voice.name} (${voice.keywords?.length || 0} 个关键词)`));
  }

  if (!state.packs.length) {
    keywordSelect.append(option("", "暂无贴纸包"));
  }
  if (!state.voices.length) {
    voiceKeywordSelect.append(option("", "暂无语音"));
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

function voiceChip(keyword, voiceId, voiceName) {
  const node = document.createElement("span");
  node.className = "chip";
  const label = document.createElement("span");
  label.textContent = String(keyword ?? "");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "×";
  remove.title = `删除 ${voiceName} 的关键词 ${keyword}`;
  remove.addEventListener("click", () => deleteVoiceKeyword(voiceId, keyword));
  node.append(label, remove);
  return node;
}

function previewUrl(stickerId) {
  return `/api/stickers/${encodeURIComponent(stickerId)}`;
}

function voiceFileUrl(voiceId) {
  return `/api/voices/${encodeURIComponent(voiceId)}/file`;
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
    item.classList.toggle("is-active-pack", pack.name === state.selectedPackName);
    item.addEventListener("dragenter", enterPackDrop);
    item.addEventListener("dragover", overPackDrop);
    item.addEventListener("dragleave", leavePackDrop);
    item.addEventListener("drop", (event) => dropInboxOnPack(event, pack));

    const head = document.createElement("div");
    head.className = "pack-head";
    const title = document.createElement("div");
    title.className = "pack-title";
    title.textContent = pack.name;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${pack.count} 张`;
    const actions = document.createElement("div");
    actions.className = "pack-actions";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "icon-danger-button";
    deleteButton.title = `删除贴纸包 ${pack.name}`;
    deleteButton.setAttribute("aria-label", `删除贴纸包 ${pack.name}`);
    deleteButton.textContent = "×";
    deleteButton.addEventListener("click", () => deletePack(pack));
    const manageButton = document.createElement("button");
    manageButton.type = "button";
    manageButton.className = "small-button";
    manageButton.textContent = "管理";
    manageButton.addEventListener("click", () => fetchPackDetail(pack.name).catch((err) => showToast(err.message, true)));
    actions.append(badge, manageButton, deleteButton);
    head.append(title, actions);

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

function packDownloadUrl(packName) {
  return `/api/packs/${encodeURIComponent(packName)}/download`;
}

function packArchiveFileName(packName) {
  return `${String(packName || "stickers").trim() || "stickers"}.7z`;
}

function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

function filenameFromDisposition(disposition, fallback) {
  const value = String(disposition || "");
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return fallback;
    }
  }
  const asciiMatch = value.match(/filename="?([^";]+)"?/i);
  return asciiMatch ? asciiMatch[1] : fallback;
}

async function downloadSelectedPackArchive() {
  const packName = state.selectedPackDetail?.name || state.selectedPackName;
  if (!packName) {
    showToast("请先选择一个贴纸包。", true);
    return;
  }

  const button = $("#packDownloadBtn");
  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = "正在打包...";
  showToast("正在打包贴纸包，请稍等。");

  try {
    const res = await fetch(packDownloadUrl(packName), { cache: "no-store" });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("请先登录。");
    }
    if (!res.ok) {
      let message = "下载贴纸包失败";
      try {
        const data = await res.json();
        message = data.error || message;
      } catch {
        message = await res.text() || message;
      }
      throw new Error(message);
    }

    button.textContent = "正在下载...";
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"), packArchiveFileName(packName));
    downloadBlob(blob, filename);
    showToast("贴纸包下载已开始。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previousText || "下载 7z";
  }
}

function getSelectedPackStickerIds() {
  return Array.from(document.querySelectorAll(".pack-sticker-check:checked")).map((input) => input.value);
}

function updatePackStickerSelectionText() {
  const selected = getSelectedPackStickerIds();
  const selectedSet = new Set(selected);
  for (const card of document.querySelectorAll(".sticker-card")) {
    card.classList.toggle("is-selected", selectedSet.has(card.dataset.stickerId));
  }
  $("#packStickerSelectedText").textContent = `已选择 ${selected.length} 张`;
  const stickers = state.selectedPackDetail?.stickers || [];
  const allBox = $("#packStickerSelectAll");
  allBox.checked = Boolean(stickers.length) && selected.length === stickers.length;
  allBox.indeterminate = selected.length > 0 && selected.length < stickers.length;
}

function renderPackDetail() {
  const detail = state.selectedPackDetail;
  const list = $("#packStickerList");
  const downloadButton = $("#packDownloadBtn");
  list.replaceChildren();

  if (!detail) {
    $("#packDetailTitle").textContent = "贴纸包内容";
    $("#packDetailMeta").textContent = "选择上方贴纸包后查看和管理具体贴纸。";
    downloadButton.hidden = true;
    list.className = "sticker-grid empty";
    list.textContent = "暂无选中的贴纸包";
    updatePackStickerSelectionText();
    return;
  }

  $("#packDetailTitle").textContent = detail.name;
  $("#packDetailMeta").textContent = `${detail.count || 0} 张贴纸 / ${detail.keywords?.length || 0} 个关键词`;
  downloadButton.hidden = false;
  downloadButton.disabled = false;
  downloadButton.textContent = "下载 7z";

  const stickers = detail.stickers || [];
  if (!stickers.length) {
    list.className = "sticker-grid empty";
    list.textContent = "这个贴纸包里暂无贴纸";
    updatePackStickerSelectionText();
    return;
  }

  list.className = "sticker-grid";
  for (const sticker of stickers) {
    const card = document.createElement("article");
    card.className = "sticker-card";
    card.dataset.stickerId = sticker.id;

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "pack-sticker-check";
    check.value = sticker.id;
    check.setAttribute("aria-label", `选择贴纸 ${sticker.original_name || sticker.id}`);
    check.addEventListener("change", updatePackStickerSelectionText);

    const frame = document.createElement("a");
    frame.className = "sticker-frame";
    frame.href = previewUrl(sticker.id);
    frame.target = "_blank";
    frame.rel = "noopener";
    const image = document.createElement("img");
    image.src = previewUrl(sticker.id);
    image.alt = sticker.original_name || sticker.id;
    image.loading = "lazy";
    image.decoding = "async";
    frame.append(image);

    const title = document.createElement("div");
    title.className = "sticker-title";
    title.textContent = sticker.original_name || sticker.file || sticker.id;

    const meta = document.createElement("div");
    meta.className = "sticker-meta";
    meta.textContent = `${formatBytes(sticker.size)} / ${formatTime(sticker.created_at)}${sticker.missing ? " / 文件缺失" : ""}`;

    card.append(check, frame, title, meta);
    list.append(card);
  }
  updatePackStickerSelectionText();
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

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function renderVoices() {
  const list = $("#voiceList");
  list.className = "pack-list";
  list.replaceChildren();

  if (!state.voices.length) {
    list.className = "pack-list empty";
    list.textContent = "暂无语音";
    return;
  }

  for (const voice of state.voices) {
    const item = document.createElement("article");
    item.className = "voice-card";

    const head = document.createElement("div");
    head.className = "pack-head";
    const title = document.createElement("div");
    title.className = "voice-title";
    title.textContent = voice.name;
    const actions = document.createElement("div");
    actions.className = "pack-actions";
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${voice.keywords?.length || 0} 个关键词`;
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "icon-danger-button";
    deleteButton.title = `删除语音 ${voice.name}`;
    deleteButton.setAttribute("aria-label", `删除语音 ${voice.name}`);
    deleteButton.textContent = "×";
    deleteButton.addEventListener("click", () => deleteVoice(voice));
    actions.append(badge, deleteButton);
    head.append(title, actions);

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = voiceFileUrl(voice.id);

    const meta = document.createElement("div");
    meta.className = "voice-meta";
    meta.textContent = `${voice.original_name || voice.file} / ${formatBytes(voice.size)}${voice.missing ? " / 文件缺失" : ""}`;

    const chips = document.createElement("div");
    chips.className = "chips";
    if (voice.keywords?.length) {
      for (const keyword of voice.keywords) {
        chips.append(voiceChip(keyword, voice.id, voice.name));
      }
    } else {
      const empty = document.createElement("span");
      empty.className = "badge";
      empty.textContent = "暂无关键词";
      chips.append(empty);
    }

    item.append(head, audio, meta, chips);
    list.append(item);
  }
}

function renderVoiceKeywords() {
  const list = $("#voiceKeywordList");
  list.className = "list";
  list.replaceChildren();

  if (!state.voiceKeywords.length) {
    list.className = "list empty";
    list.textContent = "暂无语音关键词";
    return;
  }

  for (const relation of state.voiceKeywords) {
    const item = document.createElement("article");
    item.className = "keyword-card";

    const head = document.createElement("div");
    head.className = "item-head";
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = relation.keyword;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${relation.voices.length} 条语音`;
    head.append(title, badge);

    const chips = document.createElement("div");
    chips.className = "chips";
    for (const voiceName of relation.voices) {
      const voiceNode = document.createElement("span");
      voiceNode.className = "chip";
      voiceNode.textContent = voiceName;
      chips.append(voiceNode);
    }

    item.append(head, chips);
    list.append(item);
  }
}

function inboxImageUrl(itemId) {
  return `/api/inbox/${encodeURIComponent(itemId)}/image`;
}

function formatTime(seconds) {
  if (!seconds) {
    return "未知时间";
  }
  return new Date(Number(seconds) * 1000).toLocaleString();
}

function getSelectedInboxIds() {
  return Array.from(document.querySelectorAll(".inbox-check:checked")).map((input) => input.value);
}

function updateInboxSelectionText() {
  const selected = getSelectedInboxIds();
  const selectedSet = new Set(selected);
  for (const card of document.querySelectorAll(".inbox-card")) {
    card.classList.toggle("is-selected", selectedSet.has(card.dataset.inboxId));
  }
  $("#inboxSelectedText").textContent = `已选择 ${selected.length} 个`;
  const allBox = $("#inboxSelectAll");
  allBox.checked = Boolean(state.inbox.length) && selected.length === state.inbox.length;
  allBox.indeterminate = selected.length > 0 && selected.length < state.inbox.length;
}

function inboxDragIds(itemId) {
  const selected = getSelectedInboxIds();
  return selected.includes(itemId) ? selected : [itemId];
}

function pickerPackButton(pack) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "pack-picker-item";
  button.addEventListener("click", () => movePickerItemsToPack(pack.name));

  const title = document.createElement("span");
  title.className = "pack-picker-item-title";
  title.textContent = pack.name;
  const count = document.createElement("span");
  count.className = "badge";
  count.textContent = `${pack.count} 张`;
  button.append(title, count);
  return button;
}

function renderPackPicker() {
  const search = $("#packPickerSearch").value.trim().toLowerCase();
  const allList = $("#packPickerList");
  const recentSection = $("#packPickerRecent");
  const recentList = $("#packPickerRecentList");
  const recentNames = new Set(loadRecentPackNames());
  const matchesSearch = (pack) => !search || pack.name.toLowerCase().includes(search);
  const matchingPacks = state.packs.filter(matchesSearch);
  const recentPacks = state.packs.filter((pack) => recentNames.has(pack.name) && matchesSearch(pack));

  allList.replaceChildren();
  recentList.replaceChildren();
  recentSection.hidden = !recentPacks.length;

  for (const pack of recentPacks) {
    recentList.append(pickerPackButton(pack));
  }

  if (!matchingPacks.length) {
    const empty = document.createElement("div");
    empty.className = "pack-picker-empty";
    empty.textContent = state.packs.length ? "没有匹配的贴纸包" : "暂无可选择的贴纸包";
    allList.append(empty);
    return;
  }

  for (const pack of matchingPacks) {
    allList.append(pickerPackButton(pack));
  }
}

function openPackPicker(ids) {
  const itemIds = Array.from(new Set((ids || []).map(String).filter(Boolean)));
  if (!itemIds.length) {
    showToast("请选择要整理的表情。", true);
    return;
  }
  if (!state.packs.length) {
    showToast("还没有贴纸包，请先新建一个。", true);
    return;
  }

  state.pickerInboxIds = itemIds;
  $("#packPickerTitle").textContent = `移动 ${itemIds.length} 个待整理表情`;
  $("#packPickerSummary").textContent = "选择目标贴纸包后会立即加入，关键词沿用上方输入框。";
  $("#packPickerSearch").value = "";
  renderPackPicker();

  const dialog = $("#packPickerDialog");
  dialog.showModal();
  window.setTimeout(() => $("#packPickerSearch").focus(), 0);
}

function closePackPicker() {
  const dialog = $("#packPickerDialog");
  if (dialog.open) {
    dialog.close();
  }
  state.pickerInboxIds = [];
}

async function movePickerItemsToPack(packName) {
  const ids = state.pickerInboxIds;
  closePackPicker();
  saveRecentPackName(packName);
  await assignInboxIdsToPack(ids, packName, $("#inboxKeyword").value.trim());
}

function hasInboxDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes("application/x-hikari-inbox");
}

function startInboxDrag(event, itemId) {
  const ids = inboxDragIds(itemId);
  state.draggingInboxIds = ids;
  event.currentTarget.classList.add("is-dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("application/x-hikari-inbox", JSON.stringify({ ids }));
  event.dataTransfer.setData("text/plain", ids.join(","));
}

function endInboxDrag(event) {
  event.currentTarget.classList.remove("is-dragging");
  state.draggingInboxIds = [];
  for (const card of document.querySelectorAll(".pack-card.is-drop-target")) {
    card.classList.remove("is-drop-target");
  }
}

function enterPackDrop(event) {
  if (!hasInboxDrag(event)) {
    return;
  }
  event.preventDefault();
  event.currentTarget.classList.add("is-drop-target");
}

function overPackDrop(event) {
  if (!hasInboxDrag(event)) {
    return;
  }
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
}

function leavePackDrop(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) {
    event.currentTarget.classList.remove("is-drop-target");
  }
}

async function dropInboxOnPack(event, pack) {
  if (!hasInboxDrag(event)) {
    return;
  }
  event.preventDefault();
  event.currentTarget.classList.remove("is-drop-target");

  let ids = state.draggingInboxIds;
  try {
    const payload = JSON.parse(event.dataTransfer.getData("application/x-hikari-inbox") || "{}");
    if (Array.isArray(payload.ids)) {
      ids = payload.ids.map(String).filter(Boolean);
    }
  } catch {
    ids = state.draggingInboxIds;
  }

  await assignInboxIdsToPack(ids, pack.name, $("#inboxKeyword").value.trim());
}

function renderInbox() {
  const list = $("#inboxList");
  $("#inboxCount").textContent = state.inbox.length;
  list.className = "inbox-list";
  list.replaceChildren();

  if (!state.inbox.length) {
    list.className = "inbox-list empty";
    list.textContent = "暂无待整理表情";
    updateInboxSelectionText();
    return;
  }

  for (const item of state.inbox) {
    const card = document.createElement("article");
    card.className = "inbox-card";
    card.draggable = true;
    card.dataset.inboxId = item.id;
    card.addEventListener("dragstart", (event) => startInboxDrag(event, item.id));
    card.addEventListener("dragend", endInboxDrag);

    const checkbox = document.createElement("input");
    checkbox.className = "inbox-check";
    checkbox.type = "checkbox";
    checkbox.value = item.id;
    checkbox.addEventListener("change", updateInboxSelectionText);

    const image = document.createElement("img");
    image.src = inboxImageUrl(item.id);
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    image.draggable = false;

    const meta = document.createElement("div");
    meta.className = "inbox-meta";
    const source = item.group_id ? `群 ${item.group_id}` : "私聊";
    meta.textContent = `${source} / 用户 ${item.sender_id || "未知"} / ${formatTime(item.created_at)}`;

    const moveButton = document.createElement("button");
    moveButton.type = "button";
    moveButton.className = "inbox-move-button";
    moveButton.textContent = "移动到...";
    moveButton.addEventListener("click", () => openPackPicker(inboxDragIds(item.id)));

    card.append(checkbox, image, meta, moveButton);
    list.append(card);
  }

  updateInboxSelectionText();
}

function render() {
  $("#stickerCount").textContent = state.totalStickers;
  $("#packCount").textContent = state.packs.length;
  $("#keywordCount").textContent = state.keywords.length;
  $("#voiceCount").textContent = state.totalVoices;
  $("#voiceKeywordCount").textContent = state.totalVoiceKeywords;
  renderSelects();
  renderInbox();
  renderPacks();
  renderPackDetail();
  renderKeywords();
  renderVoices();
  renderVoiceKeywords();
  renderTtsConfig();
  renderAiAgentConfig();
  if (state.accessRules.length) {
    renderAccessRules();
  }
}

function renderTtsConfig() {
  const cfg = state.ttsConfig || {};
  const fish = cfg.fish_audio || {};
  $("#ttsEnabled").checked = cfg.enabled !== false;
  const selectedVoice = $("#ttsSelectedVoice");
  selectedVoice.replaceChildren();
  for (const voice of cfg.voices || []) {
    selectedVoice.append(option(voice.name, voice.name));
  }
  selectedVoice.value = cfg.selected_voice || "";
  $("#ttsProxy").value = cfg.proxy || "";
  $("#ttsConnectTimeout").value = cfg.connect_timeout ?? 10;
  $("#ttsReceiveTimeout").value = cfg.receive_timeout ?? 60;
  $("#ttsMaxChars").value = cfg.max_chars ?? 120;
  $("#ttsCooldown").value = cfg.cooldown_seconds ?? 5;
  $("#ttsCacheDir").value = cfg.cache_dir || "/tmp/hikari_bot/tts";
  $("#ttsCacheTtl").value = cfg.cache_ttl_minutes ?? 60;
  $("#fishApiKey").value = "";
  $("#fishApiKeyHint").textContent = fish.api_key_set ? "已配置；留空保存会保留原 Key。" : "未配置";
  $("#fishModel").value = fish.model || "s2-pro";
  $("#fishBackupModel").value = fish.backup_model || "";
  $("#fishRetryCount").value = fish.retry_count ?? 3;
  $("#fishRetryDelay").value = fish.retry_delay_seconds ?? 1.0;
  $("#fishFormat").value = fish.format || "mp3";
  $("#fishLatency").value = fish.latency || "normal";
  $("#fishSpeed").value = fish.speed ?? 1.0;
  $("#fishVolume").value = fish.volume ?? 0;
  $("#fishPitch").value = fish.pitch_semitones ?? 0;
  $("#fishNormalizeLoudness").checked = fish.normalize_loudness !== false;
  $("#fishTemperature").value = fish.temperature ?? 0.7;
  $("#fishTopP").value = fish.top_p ?? 0.7;
  $("#fishChunkLength").value = fish.chunk_length ?? 300;
  $("#fishNormalize").checked = fish.normalize !== false;
  $("#fishSampleRate").value = fish.sample_rate || "";
  $("#fishMp3Bitrate").value = fish.mp3_bitrate ?? 128;
  $("#fishRepetitionPenalty").value = fish.repetition_penalty ?? 1.2;
  $("#fishConditionPrevious").checked = fish.condition_on_previous_chunks !== false;
  renderTtsVoiceList();
}

function renderTtsVoiceList() {
  const list = $("#ttsVoiceList");
  const cfg = state.ttsConfig || {};
  const voices = cfg.voices || [];
  list.replaceChildren();
  if (!voices.length) {
    list.className = "list empty";
    list.textContent = "暂无音色";
    return;
  }
  list.className = "list tts-voice-list";
  for (const voice of voices) {
    const row = document.createElement("div");
    row.className = "tts-voice-row";
    const meta = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = voice.name;
    const id = document.createElement("small");
    id.textContent = voice.reference_id;
    meta.append(name, id);
    const actions = document.createElement("div");
    actions.className = "item-actions";
    const useButton = document.createElement("button");
    useButton.type = "button";
    useButton.className = "ghost";
    useButton.textContent = voice.name === cfg.selected_voice ? "当前" : "使用";
    useButton.disabled = voice.name === cfg.selected_voice;
    useButton.addEventListener("click", () => saveTtsConfigData({ ...buildTtsPayload(), selected_voice: voice.name }));
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "ghost";
    editButton.textContent = "编辑";
    editButton.addEventListener("click", () => startTtsVoiceEdit(voice));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "删除";
    deleteButton.disabled = voices.length <= 1;
    deleteButton.addEventListener("click", () => deleteTtsVoice(voice.name));
    actions.append(useButton, editButton, deleteButton);
    row.append(meta, actions);
    list.append(row);
  }
}

function renderAiAgentConfig() {
  const cfg = state.aiagentConfig || {};
  const model = cfg.model || {};
  const persona = cfg.persona || {};
  const chat = cfg.chat || {};
  $("#aiagentEnabled").checked = cfg.enabled === true;
  $("#aiagentBaseUrl").value = model.base_url || "https://api.deepseek.com/v1";
  $("#aiagentModel").value = model.model || "deepseek-chat";
  $("#aiagentApiKey").value = "";
  $("#aiagentApiKeyHint").textContent = model.api_key_set ? "已配置；留空保存会保留原 Key。" : "未配置";
  $("#aiagentProxy").value = model.proxy || "";
  $("#aiagentTimeout").value = model.timeout_seconds ?? 60;
  $("#aiagentTemperature").value = model.temperature ?? 0.7;
  $("#aiagentTopP").value = model.top_p ?? 1.0;
  $("#aiagentMaxTokens").value = model.max_tokens ?? 1024;

  const personaPath = persona.skill_path || "BotData/agent_personas/default";
  $("#aiagentPersonaPath").value = personaPath;
  $("#aiagentPersonaMaxChars").value = persona.max_chars ?? 12000;
  $("#aiagentFallbackPrompt").value = persona.fallback_prompt || "";
  $("#aiagentMaxUserChars").value = chat.max_user_chars ?? 2000;
  $("#aiagentMaxReplyChars").value = chat.max_reply_chars ?? 3500;
  $("#aiagentMaxHistory").value = chat.max_history_messages ?? 10;
  $("#aiagentCooldown").value = chat.cooldown_seconds ?? 3;
  $("#aiagentSystemExtra").value = chat.system_prompt_extra || "";

  const select = $("#aiagentPersonaSelect");
  select.replaceChildren(option("", "手动填写路径"));
  for (const personaItem of state.aiagentPersonas || []) {
    const label = `${personaItem.title || personaItem.path} (${personaItem.path})`;
    select.append(option(personaItem.path, label));
  }
  select.value = (state.aiagentPersonas || []).some((item) => item.path === personaPath) ? personaPath : "";
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

function updateVoiceFileHint() {
  const input = $("#voiceFile");
  const hint = $("#voiceFileHint");
  const count = input.files?.length || 0;
  if (!count) {
    hint.textContent = `可多选，单次最多 ${MAX_VOICE_UPLOAD_FILES} 个文件。`;
    return;
  }

  if (count > MAX_VOICE_UPLOAD_FILES) {
    input.value = "";
    hint.textContent = `已超过上限，请重新选择 ${MAX_VOICE_UPLOAD_FILES} 个以内的文件。`;
    showToast(`一次最多上传 ${MAX_VOICE_UPLOAD_FILES} 个语音文件。`, true);
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

async function uploadVoices(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fileCount = $("#voiceFile").files?.length || 0;
  if (fileCount <= 0) {
    showToast("请选择要上传的语音文件。", true);
    return;
  }
  if (fileCount > MAX_VOICE_UPLOAD_FILES) {
    showToast(`一次最多上传 ${MAX_VOICE_UPLOAD_FILES} 个语音文件。`, true);
    return;
  }

  const submitButton = form.querySelector("button[type='submit']");
  submitButton.disabled = true;

  try {
    const res = await fetch("/api/voices", {
      method: "POST",
      body: new FormData(form),
    });
    const data = await readJsonResponse(res, "上传语音失败");
    state.voices = data.state?.voices || [];
    state.voiceKeywords = data.state?.keywords || [];
    state.totalVoices = Number(data.state?.total_voices || 0);
    state.totalVoiceKeywords = Number(data.state?.total_keywords || 0);
    form.reset();
    updateVoiceFileHint();
    render();
    showToast(data.message || "语音上传完成。", data.status === "failed");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    submitButton.disabled = false;
  }
}

function buildTtsPayload() {
  return {
    enabled: $("#ttsEnabled").checked,
    selected_voice: $("#ttsSelectedVoice").value,
    voices: (state.ttsConfig?.voices || []).map((voice) => ({ name: voice.name, reference_id: voice.reference_id })),
    fish_audio: {
      api_key: $("#fishApiKey").value.trim(),
      model: $("#fishModel").value,
      backup_model: $("#fishBackupModel").value,
      retry_count: Number($("#fishRetryCount").value || 0),
      retry_delay_seconds: Number($("#fishRetryDelay").value || 1),
      format: $("#fishFormat").value,
      latency: $("#fishLatency").value,
      speed: Number($("#fishSpeed").value || 1),
      volume: Number($("#fishVolume").value || 0),
      pitch_semitones: Number($("#fishPitch").value || 0),
      normalize_loudness: $("#fishNormalizeLoudness").checked,
      temperature: Number($("#fishTemperature").value || 0.7),
      top_p: Number($("#fishTopP").value || 0.7),
      chunk_length: Number($("#fishChunkLength").value || 300),
      normalize: $("#fishNormalize").checked,
      sample_rate: $("#fishSampleRate").value ? Number($("#fishSampleRate").value) : null,
      mp3_bitrate: Number($("#fishMp3Bitrate").value || 128),
      repetition_penalty: Number($("#fishRepetitionPenalty").value || 1.2),
      condition_on_previous_chunks: $("#fishConditionPrevious").checked,
    },
    proxy: $("#ttsProxy").value.trim(),
    connect_timeout: Number($("#ttsConnectTimeout").value || 10),
    receive_timeout: Number($("#ttsReceiveTimeout").value || 60),
    max_chars: Number($("#ttsMaxChars").value || 120),
    cooldown_seconds: Number($("#ttsCooldown").value || 5),
    cache_dir: $("#ttsCacheDir").value.trim(),
    cache_ttl_minutes: Number($("#ttsCacheTtl").value || 60),
  };
}

function buildAiAgentPayload() {
  return {
    enabled: $("#aiagentEnabled").checked,
    model: {
      base_url: $("#aiagentBaseUrl").value.trim(),
      api_key: $("#aiagentApiKey").value.trim(),
      model: $("#aiagentModel").value.trim(),
      temperature: Number($("#aiagentTemperature").value || 0.7),
      top_p: Number($("#aiagentTopP").value || 1),
      max_tokens: Number($("#aiagentMaxTokens").value || 1024),
      timeout_seconds: Number($("#aiagentTimeout").value || 60),
      proxy: $("#aiagentProxy").value.trim(),
    },
    persona: {
      skill_path: $("#aiagentPersonaPath").value.trim(),
      max_chars: Number($("#aiagentPersonaMaxChars").value || 12000),
      fallback_prompt: $("#aiagentFallbackPrompt").value.trim(),
    },
    chat: {
      max_user_chars: Number($("#aiagentMaxUserChars").value || 2000),
      max_reply_chars: Number($("#aiagentMaxReplyChars").value || 3500),
      max_history_messages: Number($("#aiagentMaxHistory").value || 10),
      cooldown_seconds: Number($("#aiagentCooldown").value || 3),
      system_prompt_extra: $("#aiagentSystemExtra").value.trim(),
    },
  };
}

async function saveAiAgentConfig(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type='submit']");
  button.disabled = true;
  try {
    const res = await fetch("/api/aiagent-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildAiAgentPayload()),
    });
    const data = await readJsonResponse(res, "保存 AI Agent 设置失败");
    state.aiagentConfig = data.config || {};
    state.aiagentPersonas = data.personas || [];
    renderAiAgentConfig();
    showToast(data.message || "AI Agent 设置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function saveTtsConfigData(payload, successMessage = "TTS 设置已保存。") {
  try {
    const res = await fetch("/api/tts-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readJsonResponse(res, "保存 TTS 设置失败");
    state.ttsConfig = data.config || {};
    renderTtsConfig();
    showToast(data.message || successMessage);
    return true;
  } catch (err) {
    showToast(err.message, true);
    return false;
  }
}

async function saveTtsConfig(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type='submit']");
  button.disabled = true;
  try {
    await saveTtsConfigData(buildTtsPayload());
  } finally {
    button.disabled = false;
  }
}

function resetTtsVoiceEdit() {
  state.editingTtsVoiceName = "";
  $("#ttsVoiceForm").reset();
  $("#ttsVoiceEditSubmit").textContent = "添加音色";
  $("#ttsVoiceEditCancel").hidden = true;
}

function startTtsVoiceEdit(voice) {
  state.editingTtsVoiceName = voice.name;
  $("#ttsVoiceEditName").value = voice.name;
  $("#ttsVoiceEditReferenceId").value = voice.reference_id;
  $("#ttsVoiceEditSubmit").textContent = "保存音色";
  $("#ttsVoiceEditCancel").hidden = false;
  $("#ttsVoiceEditName").focus();
}

async function saveTtsVoice(event) {
  event.preventDefault();
  const name = $("#ttsVoiceEditName").value.trim();
  const referenceId = $("#ttsVoiceEditReferenceId").value.trim();
  if (!name || !referenceId) {
    showToast("请填写音色名称和模型 ID。", true);
    return;
  }
  const payload = buildTtsPayload();
  const previousName = state.editingTtsVoiceName;
  const existing = payload.voices.find((voice) => voice.name === previousName);
  if (existing) {
    existing.name = name;
    existing.reference_id = referenceId;
    if (payload.selected_voice === previousName) payload.selected_voice = name;
  } else {
    payload.voices.push({ name, reference_id: referenceId });
    payload.selected_voice = name;
  }
  if (await saveTtsConfigData(payload, "音色已保存。")) resetTtsVoiceEdit();
}

async function deleteTtsVoice(name) {
  const payload = buildTtsPayload();
  if (payload.voices.length <= 1) {
    showToast("至少保留一个音色。", true);
    return;
  }
  payload.voices = payload.voices.filter((voice) => voice.name !== name);
  if (payload.selected_voice === name) payload.selected_voice = payload.voices[0].name;
  if (await saveTtsConfigData(payload, "音色已删除。") && state.editingTtsVoiceName === name) resetTtsVoiceEdit();
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

async function addVoiceKeyword(event) {
  event.preventDefault();
  const voice = $("#voiceKeywordVoice").value;
  const keyword = $("#voiceKeywordInput").value.trim();
  if (!voice || !keyword) {
    showToast("请选择语音并填写一个或多个关键词。", true);
    return;
  }

  try {
    const res = await fetch("/api/voice-keywords", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice, keyword }),
    });
    const data = await readJsonResponse(res, "添加语音关键词失败");
    state.voices = data.voices || [];
    state.voiceKeywords = data.keywords || [];
    state.totalVoices = Number(data.total_voices || 0);
    state.totalVoiceKeywords = Number(data.total_keywords || 0);
    $("#voiceKeywordInput").value = "";
    render();
    showToast("语音关键词关联已添加。");
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
    if (state.selectedPackName === pack.name) {
      state.selectedPackName = "";
      state.selectedPackDetail = null;
    }
    render();
    showToast("关键词关联已删除。");
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteVoiceKeyword(voice, keyword) {
  try {
    const params = new URLSearchParams({ voice, keyword });
    const res = await fetch(`/api/voice-keywords?${params}`, { method: "DELETE" });
    const data = await readJsonResponse(res, "删除语音关键词失败");
    state.voices = data.voices || [];
    state.voiceKeywords = data.keywords || [];
    state.totalVoices = Number(data.total_voices || 0);
    state.totalVoiceKeywords = Number(data.total_keywords || 0);
    render();
    showToast("语音关键词关联已删除。");
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deletePack(pack) {
  const confirmed = window.confirm(`确定删除贴纸包「${pack.name}」吗？\n将移除这个贴纸包，并删除不再被其他贴纸包引用的本地文件。`);
  if (!confirmed) {
    return;
  }

  try {
    const params = new URLSearchParams({ pack: pack.name });
    const res = await fetch(`/api/packs?${params}`, { method: "DELETE" });
    const data = await readJsonResponse(res, "删除贴纸包失败");
    state.packs = data.packs || [];
    state.keywords = data.keywords || [];
    state.totalStickers = Number(data.total_stickers || 0);
    render();
    const result = data.result || {};
    showToast(`已删除 ${result.pack || pack.name}，移除 ${result.removed_stickers || 0} 个关联，删除 ${result.deleted_files || 0} 个本地文件。`);
  } catch (err) {
    showToast(err.message, true);
  }
}

function togglePackStickerSelection(event) {
  const checked = event.currentTarget.checked;
  for (const input of document.querySelectorAll(".pack-sticker-check")) {
    input.checked = checked;
  }
  updatePackStickerSelectionText();
}

async function deleteSelectedPackStickers() {
  const packName = state.selectedPackDetail?.name || state.selectedPackName;
  const stickerIds = getSelectedPackStickerIds();
  if (!packName) {
    showToast("请先选择一个贴纸包。", true);
    return;
  }
  if (!stickerIds.length) {
    showToast("请选择要删除的贴纸。", true);
    return;
  }
  const confirmed = window.confirm(`确定从「${packName}」删除 ${stickerIds.length} 张贴纸吗？\n如果这些贴纸没有被其他贴纸包引用，本地文件也会被删除。`);
  if (!confirmed) {
    return;
  }

  const button = $("#packStickerDeleteBtn");
  button.disabled = true;
  try {
    const res = await fetch("/api/pack-stickers/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack: packName, stickers: stickerIds }),
    });
    const data = await readJsonResponse(res, "删除贴纸失败");
    state.packs = data.packs || [];
    state.keywords = data.keywords || [];
    state.totalStickers = Number(data.total_stickers || 0);
    state.selectedPackDetail = data.pack_detail || null;
    state.selectedPackName = state.selectedPackDetail?.name || packName;
    render();
    const result = data.result || {};
    showToast(`已删除 ${result.removed || 0} 张贴纸，删除 ${result.deleted_files || 0} 个本地文件。`);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function moveSelectedPackStickers() {
  const sourcePack = state.selectedPackDetail?.name || state.selectedPackName;
  const targetPack = $("#packStickerMoveNewPack").value.trim() || $("#packStickerMoveTarget").value;
  const stickerIds = getSelectedPackStickerIds();
  if (!sourcePack) {
    showToast("请先选择一个贴纸包。", true);
    return;
  }
  if (!stickerIds.length) {
    showToast("请选择要移动的贴纸。", true);
    return;
  }
  if (!targetPack) {
    showToast("请选择目标贴纸包，或输入新贴纸包名称。", true);
    return;
  }

  const button = $("#packStickerMoveBtn");
  button.disabled = true;
  try {
    const res = await fetch("/api/pack-stickers/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_pack: sourcePack,
        target_pack: targetPack,
        stickers: stickerIds,
      }),
    });
    const data = await readJsonResponse(res, "移动贴纸失败");
    state.packs = data.packs || [];
    state.keywords = data.keywords || [];
    state.totalStickers = Number(data.total_stickers || 0);
    state.selectedPackDetail = data.pack_detail || null;
    state.selectedPackName = state.selectedPackDetail?.name || sourcePack;
    $("#packStickerMoveNewPack").value = "";
    render();
    const result = data.result || {};
    showToast(`已移动 ${result.moved || 0} 张贴纸到 ${result.target || targetPack}。`);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function deleteVoice(voice) {
  const confirmed = window.confirm(`确定删除语音「${voice.name}」吗？\n会移除这个语音及其所有关键词。`);
  if (!confirmed) {
    return;
  }

  try {
    const params = new URLSearchParams({ voice: voice.id });
    const res = await fetch(`/api/voices?${params}`, { method: "DELETE" });
    const data = await readJsonResponse(res, "删除语音失败");
    state.voices = data.voices || [];
    state.voiceKeywords = data.keywords || [];
    state.totalVoices = Number(data.total_voices || 0);
    state.totalVoiceKeywords = Number(data.total_keywords || 0);
    render();
    showToast(`已删除 ${voice.name}。`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function assignInboxIdsToPack(ids, pack, keyword, { clearInputs = false } = {}) {
  const itemIds = Array.from(new Set((ids || []).map(String).filter(Boolean)));
  if (!itemIds.length) {
    showToast("请选择要整理的表情。", true);
    return;
  }
  if (!pack) {
    showToast("请选择已有贴纸包，或输入新贴纸包名称。", true);
    return;
  }

  try {
    const res = await fetch("/api/inbox/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: itemIds, pack, keyword }),
    });
    const data = await readJsonResponse(res, "整理失败");
    state.packs = data.state?.packs || state.packs;
    state.keywords = data.state?.keywords || state.keywords;
    state.totalStickers = Number(data.state?.total_stickers || state.totalStickers);
    state.inbox = data.inbox?.items || [];
    if (clearInputs) {
      $("#inboxNewPack").value = "";
      $("#inboxKeyword").value = "";
    }
    render();
    showToast(`已加入 ${pack}：${data.result?.assigned || 0} 个。`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function assignInboxItems(event) {
  event.preventDefault();
  const ids = getSelectedInboxIds();
  const pack = $("#inboxNewPack").value.trim() || $("#inboxPack").value;
  const keyword = $("#inboxKeyword").value.trim();
  await assignInboxIdsToPack(ids, pack, keyword, { clearInputs: true });
}

async function deleteInboxItems() {
  const ids = getSelectedInboxIds();
  if (!ids.length) {
    showToast("请选择要删除的表情。", true);
    return;
  }

  try {
    const res = await fetch("/api/inbox/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const data = await readJsonResponse(res, "删除失败");
    state.inbox = data.inbox?.items || [];
    render();
    showToast(`已删除 ${data.removed || 0} 个待整理表情。`);
  } catch (err) {
    showToast(err.message, true);
  }
}

function toggleInboxSelection(event) {
  const checked = event.currentTarget.checked;
  for (const input of document.querySelectorAll(".inbox-check")) {
    input.checked = checked;
  }
  updateInboxSelectionText();
}

$("#keywordForm").addEventListener("submit", addKeyword);
$("#packStickerActionForm").addEventListener("submit", (event) => event.preventDefault());
$("#uploadForm").addEventListener("submit", uploadStickers);
$("#tgForm").addEventListener("submit", importTelegramStickers);
$("#voiceUploadForm").addEventListener("submit", uploadVoices);
$("#voiceKeywordForm").addEventListener("submit", addVoiceKeyword);
$("#ttsConfigForm").addEventListener("submit", saveTtsConfig);
$("#aiagentConfigForm").addEventListener("submit", saveAiAgentConfig);
$("#accessRulesForm").addEventListener("submit", saveAccessRules);
$("#ttsVoiceForm").addEventListener("submit", saveTtsVoice);
$("#ttsVoiceEditCancel").addEventListener("click", resetTtsVoiceEdit);
$("#inboxForm").addEventListener("submit", assignInboxItems);
$("#inboxDeleteBtn").addEventListener("click", deleteInboxItems);
$("#inboxSelectAll").addEventListener("change", toggleInboxSelection);
$("#packStickerSelectAll").addEventListener("change", togglePackStickerSelection);
$("#packStickerMoveBtn").addEventListener("click", moveSelectedPackStickers);
$("#packStickerDeleteBtn").addEventListener("click", deleteSelectedPackStickers);
$("#packDownloadBtn").addEventListener("click", downloadSelectedPackArchive);
$("#packPickerClose").addEventListener("click", closePackPicker);
$("#packPickerSearch").addEventListener("input", renderPackPicker);
$("#packPickerDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) {
    closePackPicker();
  }
});
$("#packPickerDialog").addEventListener("close", () => {
  state.pickerInboxIds = [];
});
$("#refreshBtn").addEventListener("click", () => fetchState().then(() => showToast("已刷新。")).catch((err) => showToast(err.message, true)));
$("#configRefreshBtn").addEventListener("click", () => fetchConfigFiles().then(() => showToast("配置列表已刷新。")).catch((err) => showToast(err.message, true)));
$("#configSaveBtn").addEventListener("click", () => saveCurrentConfig());
$("#logRefreshBtn").addEventListener("click", () => fetchLogFiles().then(() => showToast("日志列表已刷新。")).catch((err) => showToast(err.message, true)));
$("#logReloadBtn").addEventListener("click", () => reloadSelectedLog().catch((err) => showToast(err.message, true)));
$("#accessRefreshBtn").addEventListener("click", () => fetchAccessRules().then(() => showToast("权限规则已刷新。")).catch((err) => showToast(err.message, true)));
$("#aiagentPersonaSelect").addEventListener("change", (event) => {
  if (event.currentTarget.value) {
    $("#aiagentPersonaPath").value = event.currentTarget.value;
  }
});
$("#file").addEventListener("change", updateFileHint);
$("#voiceFile").addEventListener("change", updateVoiceFileHint);

initNavigation();
initSidebar();
fetchState().catch((err) => showToast(err.message, true));
