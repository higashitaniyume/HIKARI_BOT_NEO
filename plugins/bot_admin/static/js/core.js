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
  aiagentTools: [],
  pushConfig: {},
  pushSources: [],
  selectedPushJobId: "",
  rssConfig: {},
  selectedRssSubscriptionId: "",
  accessRules: [],
  selectedAccessPlugin: "",
  systemProbe: null,
  systemProbeError: "",
  editingTtsVoiceName: "",
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
  push: "推送",
  rss: "RSS",
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
  if (target === "push" && !Object.keys(state.pushConfig || {}).length) {
    fetchPushConfig().catch((err) => showToast(err.message, true));
  }
  if (target === "rss" && !Object.keys(state.rssConfig || {}).length) {
    fetchRssConfig().catch((err) => showToast(err.message, true));
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
