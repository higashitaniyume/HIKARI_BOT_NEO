// AstrBot 插件管理 — 列表加载、详情渲染、配置表单、操作响应

let astrbotState = {
  plugins: [],
  selectedName: "",
};

// ============================================================
// API calls
// ============================================================

async function fetchAstrbotPlugins() {
  const res = await fetch("/api/astrbot/plugins", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取插件列表失败");
  astrbotState.plugins = data || [];
  renderAstrbotPluginList();
  // If selection still valid, refresh detail
  if (astrbotState.selectedName && data.some((p) => p.name === astrbotState.selectedName)) {
    await openAstrbotPlugin(astrbotState.selectedName);
  } else {
    astrbotState.selectedName = "";
    renderAstrbotDetail();
  }
}

async function openAstrbotPlugin(name) {
  astrbotState.selectedName = name;
  renderAstrbotPluginList();
  const res = await fetch(`/api/astrbot/plugins/${encodeURIComponent(name)}`, { cache: "no-store" });
  const data = await readJsonResponse(res, "读取插件详情失败");
  renderAstrbotDetail(data);
}

async function saveAstrbotConfig() {
  const name = astrbotState.selectedName;
  if (!name) {
    showToast("请先选择一个插件。", true);
    return;
  }

  const form = $("#astrbotConfigFields");
  const inputs = form.querySelectorAll("[data-config-key]");
  const config = {};
  for (const input of inputs) {
    const key = input.dataset.configKey;
    const type = input.dataset.configType || "string";
    let val = input.value;
    if (type === "int") val = parseInt(val, 10) || 0;
    else if (type === "float") val = parseFloat(val) || 0;
    else if (type === "bool") val = input.checked;
    else if (type === "list") {
      try { val = JSON.parse(val); } catch { val = []; }
    }
    config[key] = val;
  }

  const btn = $("#astrbotConfigSaveBtn");
  btn.disabled = true;
  setAstrbotStatus("保存中...", false, "astrbotConfigStatus");
  try {
    const res = await fetch("/api/astrbot/plugins/save-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config }),
    });
    const data = await readJsonResponse(res, "保存配置失败");
    setAstrbotStatus("✅ " + (data.message || "配置已保存。"), false, "astrbotConfigStatus");
    renderAstrbotDetail(data);
    showToast(data.message || "配置已保存。");
  } catch (err) {
    setAstrbotStatus("❌ " + err.message, true, "astrbotConfigStatus");
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function reloadAstrbotPlugin(name) {
  const target = name || astrbotState.selectedName;
  if (!target) {
    showToast("请先选择一个插件。", true);
    return;
  }
  const btn = $("#astrbotReloadBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/astrbot/plugins/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: target }),
    });
    const data = await readJsonResponse(res, "重载插件失败");
    showToast(data.message || "插件已重新加载。");
    await fetchAstrbotPlugins();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function removeAstrbotPlugin(name) {
  const target = name || astrbotState.selectedName;
  if (!target) {
    showToast("请先选择一个插件。", true);
    return;
  }
  if (!confirm(`确定要卸载插件「${target}」吗？`)) return;

  const btn = $("#astrbotRemoveBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/astrbot/plugins/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: target }),
    });
    const data = await readJsonResponse(res, "卸载插件失败");
    showToast(data.message || "插件已卸载。");
    if (astrbotState.selectedName === target) {
      astrbotState.selectedName = "";
    }
    await fetchAstrbotPlugins();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function loadAstrbotFromPath() {
  const path = $("#astrbotLoadPath").value.trim();
  if (!path) {
    showToast("请输入插件路径或 zip 文件路径。", true);
    return;
  }
  const name = $("#astrbotLoadName").value.trim() || null;

  const btn = $("#astrbotLoadFromPathBtn");
  btn.disabled = true;
  setAstrbotStatus("加载中...", false, "astrbotLoadStatus");
  try {
    const res = await fetch("/api/astrbot/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, name }),
    });
    const data = await readJsonResponse(res, "加载插件失败");
    setAstrbotStatus("✅ " + (data.message || "插件已加载。"), false, "astrbotLoadStatus");
    showToast(data.message || "插件已加载。");
    await fetchAstrbotPlugins();
    // Select the newly loaded plugin
    await openAstrbotPlugin(data.name || name);
  } catch (err) {
    setAstrbotStatus("❌ " + err.message, true, "astrbotLoadStatus");
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function rebuildAstrbotEnv() {
  if (!confirm("确定要重建 AstrBot 插件公共虚拟环境吗？这可能需要几分钟。")) return;

  const btn = $("#astrbotRebuildEnvBtn");
  btn.disabled = true;
  setAstrbotStatus("重建中...", false, "astrbotEnvStatus");
  try {
    const res = await fetch("/api/astrbot/rebuild-env", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await readJsonResponse(res, "重建环境失败");
    setAstrbotStatus("✅ " + (data.message || "环境已重建。"), false, "astrbotEnvStatus");
    showToast(data.message || "环境已重建。");
  } catch (err) {
    setAstrbotStatus("❌ " + err.message, true, "astrbotEnvStatus");
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ============================================================
// Rendering
// ============================================================

function renderAstrbotPluginList() {
  const list = $("#astrbotPluginList");
  list.replaceChildren();
  if (!astrbotState.plugins.length) {
    list.className = "ops-list empty";
    list.textContent = "暂无 AstrBot 插件";
    return;
  }
  list.className = "ops-list";

  const icons = { loaded: "✅", discovered: "⏹️" };
  for (const plugin of astrbotState.plugins) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ops-list-item";
    btn.classList.toggle("is-active", plugin.name === astrbotState.selectedName);
    btn.addEventListener("click", () => openAstrbotPlugin(plugin.name).catch((err) => showToast(err.message, true)));

    const iconSpan = document.createElement("span");
    iconSpan.className = "ops-list-icon";
    iconSpan.textContent = icons[plugin.status] || "❓";

    const title = document.createElement("span");
    title.className = "ops-list-title";
    title.textContent = plugin.display_name || plugin.name;

    const meta = document.createElement("span");
    meta.className = "ops-list-meta";
    const parts = [plugin.status === "loaded" ? "已加载" : "未加载"];
    if (plugin.commands && plugin.commands.length) {
      parts.push(plugin.commands.length + " 命令");
    }
    meta.textContent = parts.join(" / ");

    btn.append(iconSpan, title, meta);
    list.append(btn);
  }
}

function renderAstrbotDetail(data) {
  const panel = $("#astrbotDetailPanel");
  const title = $("#astrbotDetailTitle");
  const meta = $("#astrbotDetailMeta");
  const actions = $("#astrbotDetailActions");
  const loadBtn = $("#astrbotLoadBtn");
  const configForm = $("#astrbotConfigForm");
  const configFields = $("#astrbotConfigFields");
  const reqSection = $("#astrbotRequirements");
  const reqContent = $("#astrbotRequirementsContent");

  if (!astrbotState.selectedName) {
    panel.hidden = true;
    return;
  }

  panel.hidden = false;

  if (!data) {
    // No detail yet — just show name
    title.textContent = astrbotState.selectedName;
    meta.textContent = "加载中...";
    actions.hidden = true;
    configForm.hidden = true;
    reqSection.hidden = true;
    return;
  }

  const isLoaded = data.status === "loaded";
  title.textContent = data.display_name || data.name;
  const metaParts = [isLoaded ? "已加载" : "发现但未加载"];
  if (data.commands && data.commands.length) {
    metaParts.push("命令: " + data.commands.map((c) => "/" + c).join(", "));
  }
  if (data.author) {
    metaParts.push("作者: " + data.author);
  }
  meta.textContent = metaParts.join(" / ");

  // Action buttons
  actions.hidden = false;
  loadBtn.hidden = isLoaded;
  if (isLoaded) {
    loadBtn.removeEventListener("click", doLoad);
  } else {
    loadBtn.addEventListener("click", doLoad);
  }

  // Config form
  if (data.schema && Object.keys(data.schema).length > 0) {
    configForm.hidden = false;
    renderAstrbotConfigForm(data.schema, data.config || {});
  } else {
    configForm.hidden = true;
  }

  // Requirements
  if (data.requirements) {
    reqSection.hidden = false;
    reqContent.textContent = data.requirements;
  } else {
    reqSection.hidden = true;
  }

  function doLoad() {
    const path = data.path;
    loadBtn.disabled = true;
    fetch("/api/astrbot/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, name: data.name }),
    })
      .then((res) => readJsonResponse(res, "加载插件失败"))
      .then(() => fetchAstrbotPlugins())
      .then(() => openAstrbotPlugin(data.name))
      .catch((err) => showToast(err.message, true))
      .finally(() => { loadBtn.disabled = false; });
  }
}

function renderAstrbotConfigForm(schema, currentConfig) {
  const container = $("#astrbotConfigFields");
  container.replaceChildren();

  for (const [key, def] of Object.entries(schema)) {
    if (typeof def !== "object" || def === null) continue;
    const type = def.type || "string";
    const description = def.description || key;
    const currentValue = currentConfig[key] !== undefined ? currentConfig[key] : def.default;

    const row = document.createElement("div");
    row.className = "ops-form-row";

    const label = document.createElement("label");
    label.htmlFor = "cfg_" + key;
    label.textContent = description;

    let input;

    if (type === "bool") {
      // Checkbox
      const wrap = document.createElement("div");
      wrap.className = "ops-form-check-wrap";

      input = document.createElement("input");
      input.type = "checkbox";
      input.id = "cfg_" + key;
      input.checked = currentValue === true;
      input.dataset.configKey = key;
      input.dataset.configType = type;

      const labelInline = document.createElement("label");
      labelInline.htmlFor = "cfg_" + key;
      labelInline.className = "ops-form-check-label";
      labelInline.textContent = key;

      wrap.append(input, labelInline);
      row.append(label, wrap);
    } else if (type === "int" || type === "float") {
      input = document.createElement("input");
      input.type = "number";
      input.id = "cfg_" + key;
      input.value = currentValue ?? 0;
      input.dataset.configKey = key;
      input.dataset.configType = type;
      if (type === "float") {
        input.step = "any";
      }
      row.append(label, input);
    } else if (type === "list" || type === "object") {
      input = document.createElement("textarea");
      input.id = "cfg_" + key;
      input.rows = 3;
      input.className = "code-editor";
      input.value = JSON.stringify(currentValue, null, 2) || "[]";
      input.dataset.configKey = key;
      input.dataset.configType = type;
      row.append(label, input);
    } else {
      // string, text, file, etc.
      input = document.createElement("input");
      input.type = "text";
      input.id = "cfg_" + key;
      input.value = currentValue ?? "";
      input.dataset.configKey = key;
      input.dataset.configType = type;
      if (type === "text") {
        input.className = "ops-form-wide";
      }
      row.append(label, input);
    }

    container.appendChild(row);
  }
}

function setAstrbotStatus(text, isError, elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? "var(--danger, #e74c3c)" : "var(--muted, #888)";
}

// ============================================================
// Init (called from main.js after nav setup)
// ============================================================

function initAstrbot() {
  $("#astrbotRefreshBtn").addEventListener("click", () =>
    fetchAstrbotPlugins().then(() => showToast("已刷新。")).catch((err) => showToast(err.message, true))
  );
  $("#astrbotReloadBtn").addEventListener("click", () =>
    reloadAstrbotPlugin().catch((err) => showToast(err.message, true))
  );
  $("#astrbotRemoveBtn").addEventListener("click", () =>
    removeAstrbotPlugin().catch((err) => showToast(err.message, true))
  );
  $("#astrbotConfigSaveBtn").addEventListener("click", () =>
    saveAstrbotConfig().catch((err) => showToast(err.message, true))
  );
  $("#astrbotLoadFromPathBtn").addEventListener("click", () =>
    loadAstrbotFromPath().catch((err) => showToast(err.message, true))
  );
  $("#astrbotRebuildEnvBtn").addEventListener("click", () =>
    rebuildAstrbotEnv().catch((err) => showToast(err.message, true))
  );
}

// Register into the VIEW_TITLES and setView lazy-load hook
(function extendCore() {
  if (typeof VIEW_TITLES !== "undefined") {
    VIEW_TITLES.astrbot = "AstrBot";
  }
  // Hook into setView — use a MutationObserver to detect view changes
  const origSetView = window.setView;
  if (origSetView) {
    const orig = origSetView;
    window.setView = function (view) {
      orig(view);
      if (view === "astrbot" && !astrbotState.plugins.length) {
        fetchAstrbotPlugins().catch((err) => showToast(err.message, true));
      }
    };
  }
})();

// Auto-init when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAstrbot);
} else {
  initAstrbot();
}
