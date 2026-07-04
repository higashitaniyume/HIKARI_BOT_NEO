async function fetchState() {
  const res = await fetch("/api/state", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取贴纸数据失败");
  state.packs = data.packs || [];
  state.keywords = data.keywords || [];
  state.totalStickers = Number(data.total_stickers || 0);
  try {
    await fetchVersionInfo(false);
  } catch (err) {
    state.versionError = err.message;
  }
  await fetchVoiceState(false);
  await fetchTtsConfig(false);
  await fetchAiAgentConfig(false);
  await fetchInbox(false);
  try {
    await fetchSystemProbe(false);
  } catch (err) {
    state.systemProbeError = err.message;
  }
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
  state.aiagentTools = data.tools_catalog || [];
  if (shouldRender) {
    renderAiAgentConfig();
  }
}

async function fetchPushConfig(shouldRender = true) {
  const res = await fetch("/api/push-config", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取推送配置失败");
  state.pushConfig = data.config || {};
  state.pushSources = data.sources || [];
  const jobs = Array.isArray(state.pushConfig.jobs) ? state.pushConfig.jobs : [];
  if (!state.selectedPushJobId || !jobs.some((job) => job.id === state.selectedPushJobId)) {
    state.selectedPushJobId = jobs[0]?.id || "";
  }
  if (shouldRender) {
    renderPushConfig();
  }
}

async function fetchRssConfig(shouldRender = true) {
  const res = await fetch("/api/rss-config", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取 RSS 订阅设置失败");
  state.rssConfig = data.config || {};
  const subscriptions = Array.isArray(state.rssConfig.subscriptions) ? state.rssConfig.subscriptions : [];
  if (!state.selectedRssSubscriptionId || !subscriptions.some((item) => item.id === state.selectedRssSubscriptionId)) {
    state.selectedRssSubscriptionId = subscriptions[0]?.id || "";
  }
  if (shouldRender) {
    renderRssConfig();
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

async function fetchSystemProbe(shouldRender = true) {
  const res = await fetch("/api/system-probe", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取系统性能探针失败");
  state.systemProbe = data || null;
  state.systemProbeError = "";
  if (shouldRender) {
    renderSystemProbe();
  }
}

async function fetchVersionInfo(shouldRender = true) {
  const res = await fetch("/api/version", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取版本信息失败");
  state.versionInfo = data || null;
  state.versionError = "";
  if (shouldRender) {
    renderVersionInfo();
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
