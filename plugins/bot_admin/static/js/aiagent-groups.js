// 群聊主动接话：群列表（逐群开关）+ 从机器人所在群挑选。
// 数据保存在 aiagent.json 的 auto_reply.groups，条目形如 {"id": "123", "enabled": true}。

const _aiagentGroupPicker = {
  groups: [],
  selected: new Set(),
  loading: false,
  error: "",
  fetchedAt: 0,
};

function normalizeAiAgentAutoReplyGroups(value) {
  let raw = value;
  if (typeof raw === "string") {
    raw = raw.split(/[\s,，]+/);
  }
  if (!Array.isArray(raw)) return [];

  const entries = [];
  const index = new Map();
  for (const item of raw) {
    let id = "";
    let enabled = true;
    if (item && typeof item === "object") {
      id = String(item.id || "").trim();
      enabled = item.enabled !== false;
    } else {
      id = String(item || "").trim();
    }
    if (!/^\d+$/.test(id)) continue;
    const entry = { id, enabled };
    if (index.has(id)) {
      entries[index.get(id)] = entry;
    } else {
      index.set(id, entries.length);
      entries.push(entry);
    }
  }
  return entries;
}

function aiagentAutoReplyEntries() {
  if (!Array.isArray(state.aiagentAutoReplyGroups)) {
    state.aiagentAutoReplyGroups = [];
  }
  return state.aiagentAutoReplyGroups;
}

function renderAiAgentAutoReplyGroups() {
  const list = $("#aiagentAutoReplyGroupList");
  if (!list) return;
  const entries = aiagentAutoReplyEntries();
  list.replaceChildren();

  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "aiagent-group-empty";
    empty.textContent = "还没有添加群，先加一个群号，或从机器人所在群选择。";
    list.append(empty);
  } else {
    entries.forEach((entry, index) => {
      const row = document.createElement("div");
      row.className = "aiagent-group-row";

      const toggle = document.createElement("label");
      toggle.className = "checkbox-field aiagent-group-toggle";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = entry.enabled !== false;
      checkbox.addEventListener("change", () => {
        entry.enabled = checkbox.checked;
        updateAiAgentAutoReplyCount();
      });
      const toggleLabel = document.createElement("span");
      toggleLabel.textContent = checkbox.checked ? "开启" : "关闭";
      checkbox.addEventListener("change", () => {
        toggleLabel.textContent = checkbox.checked ? "开启" : "关闭";
      });
      toggle.append(checkbox, toggleLabel);

      const name = document.createElement("span");
      name.className = "aiagent-group-name";
      name.textContent = entry.name || entry.id;

      const idText = document.createElement("code");
      idText.className = "aiagent-group-id";
      idText.textContent = entry.id;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger-button aiagent-group-remove";
      remove.textContent = "删除";
      remove.addEventListener("click", () => {
        const current = aiagentAutoReplyEntries();
        const position = current.findIndex((item) => item.id === entry.id);
        if (position >= 0) current.splice(position, 1);
        renderAiAgentAutoReplyGroups();
      });

      row.append(toggle, name, idText, remove);
      list.append(row);
    });
  }

  updateAiAgentAutoReplyCount();
}

function updateAiAgentAutoReplyCount() {
  const label = $("#aiagentAutoReplyGroupCount");
  if (!label) return;
  const entries = aiagentAutoReplyEntries();
  const enabled = entries.filter((entry) => entry.enabled !== false).length;
  label.textContent = `当前 ${entries.length} 个群，${enabled} 个开启`;
}

function addAiAgentAutoReplyGroup(rawId, name = "") {
  const id = String(rawId || "").trim();
  if (!/^\d+$/.test(id)) {
    showToast("群号必须是纯数字。", true);
    return false;
  }
  const entries = aiagentAutoReplyEntries();
  if (entries.some((entry) => entry.id === id)) {
    showToast("这个群已经在列表里了。", true);
    return false;
  }
  entries.push({ id, enabled: true, name });
  renderAiAgentAutoReplyGroups();
  return true;
}

function collectAiAgentAutoReplyGroups() {
  return aiagentAutoReplyEntries().map((entry) => ({ id: entry.id, enabled: entry.enabled !== false }));
}

function aiagentGroupLabel(group) {
  const members = Number(group.member_count || 0);
  return members > 0 ? `${group.name}（${members} 人）` : group.name;
}

function setAiAgentPickerStatus(text, isError = false) {
  const target = $("#aiagentAutoReplyPickerStatus");
  if (!target) return;
  target.textContent = text || "";
  target.classList.toggle("is-error", Boolean(isError));
}

async function openAiAgentAutoReplyPicker() {
  const dialog = $("#aiagentAutoReplyPickerDialog");
  if (!dialog) return;
  _aiagentGroupPicker.selected.clear();
  _aiagentGroupPicker.error = "";
  const search = $("#aiagentAutoReplyPickerSearch");
  if (search) search.value = "";
  setAiAgentPickerStatus("");
  dialog.showModal();
  await loadAiAgentGroupDirectory();
  renderAiAgentAutoReplyPicker();
}

async function loadAiAgentGroupDirectory(force = false) {
  const cacheFresh = Date.now() - _aiagentGroupPicker.fetchedAt < 30000;
  if (!force && cacheFresh && _aiagentGroupPicker.groups.length) return;

  _aiagentGroupPicker.loading = true;
  _aiagentGroupPicker.error = "";
  setAiAgentPickerStatus("正在读取群列表...");
  try {
    const res = await fetch("/api/groups", { cache: "no-store" });
    const data = await readJsonResponse(res, "读取群列表失败");
    _aiagentGroupPicker.groups = Array.isArray(data.groups) ? data.groups : [];
    _aiagentGroupPicker.fetchedAt = Date.now();
  } catch (err) {
    _aiagentGroupPicker.groups = [];
    _aiagentGroupPicker.error = err.message;
    setAiAgentPickerStatus(err.message, true);
  } finally {
    _aiagentGroupPicker.loading = false;
  }
}

function renderAiAgentAutoReplyPicker() {
  const list = $("#aiagentAutoReplyPickerList");
  const summary = $("#aiagentAutoReplyPickerSummary");
  if (!list) return;

  const keyword = String($("#aiagentAutoReplyPickerSearch")?.value || "").trim().toLowerCase();
  const entries = aiagentAutoReplyEntries();
  const existing = new Set(entries.map((entry) => entry.id));
  const groups = _aiagentGroupPicker.groups.filter((group) => {
    if (!keyword) return true;
    return group.id.includes(keyword) || String(group.name || "").toLowerCase().includes(keyword);
  });

  if (summary) {
    summary.textContent = _aiagentGroupPicker.loading
      ? "正在读取群列表..."
      : `机器人所在群 ${_aiagentGroupPicker.groups.length} 个，已添加 ${existing.size} 个`;
  }

  list.replaceChildren();
  if (!groups.length) {
    const empty = document.createElement("p");
    empty.className = "pack-picker-empty";
    empty.textContent = _aiagentGroupPicker.error
      ? _aiagentGroupPicker.error
      : keyword
        ? "没有匹配的群。"
        : "机器人还没有加入任何群。";
    list.append(empty);
    return;
  }

  for (const group of groups) {
    const added = existing.has(group.id);
    const row = document.createElement("label");
    row.className = "pack-picker-item aiagent-group-picker-item";
    row.classList.toggle("is-disabled", added);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = added || _aiagentGroupPicker.selected.has(group.id);
    checkbox.disabled = added;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) _aiagentGroupPicker.selected.add(group.id);
      else _aiagentGroupPicker.selected.delete(group.id);
      setAiAgentPickerStatus(`已选 ${_aiagentGroupPicker.selected.size} 个群`);
    });

    const text = document.createElement("span");
    text.className = "pack-picker-item-title";
    text.textContent = aiagentGroupLabel(group) + (added ? " · 已添加" : "");

    row.append(checkbox, text);
    list.append(row);
  }
  setAiAgentPickerStatus(
    _aiagentGroupPicker.selected.size ? `已选 ${_aiagentGroupPicker.selected.size} 个群` : "",
  );
}

function closeAiAgentAutoReplyPicker() {
  const dialog = $("#aiagentAutoReplyPickerDialog");
  if (dialog) dialog.close();
}

function confirmAiAgentAutoReplyPicker() {
  const groups = _aiagentGroupPicker.groups;
  const byId = new Map(groups.map((group) => [group.id, group]));
  let added = 0;
  for (const id of _aiagentGroupPicker.selected) {
    const group = byId.get(id);
    if (addAiAgentAutoReplyGroup(id, group ? group.name : "")) added += 1;
  }
  _aiagentGroupPicker.selected.clear();
  if (added > 0) {
    showToast(`已添加 ${added} 个群，记得保存 AI Agent 设置。`);
    closeAiAgentAutoReplyPicker();
  } else {
    setAiAgentPickerStatus("没有新增群。", true);
  }
}

function bindAiAgentAutoReplyGroupControls() {
  $("#aiagentAutoReplyAddBtn")?.addEventListener("click", () => {
    const input = $("#aiagentAutoReplyGroupInput");
    if (addAiAgentAutoReplyGroup(input.value)) input.value = "";
  });
  $("#aiagentAutoReplyGroupInput")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const input = event.currentTarget;
    if (addAiAgentAutoReplyGroup(input.value)) input.value = "";
  });
  $("#aiagentAutoReplyPickBtn")?.addEventListener("click", () => {
    openAiAgentAutoReplyPicker().catch((err) => showToast(err.message, true));
  });
  $("#aiagentAutoReplyPickerClose")?.addEventListener("click", closeAiAgentAutoReplyPicker);
  $("#aiagentAutoReplyPickerConfirm")?.addEventListener("click", confirmAiAgentAutoReplyPicker);
  $("#aiagentAutoReplyPickerSearch")?.addEventListener("input", renderAiAgentAutoReplyPicker);
  $("#aiagentAutoReplyPickerDialog")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeAiAgentAutoReplyPicker();
  });
  $("#aiagentAutoReplyPickerDialog")?.addEventListener("close", () => {
    _aiagentGroupPicker.selected.clear();
  });
}

bindAiAgentAutoReplyGroupControls();
