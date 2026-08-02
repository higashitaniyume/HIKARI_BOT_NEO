/* AI 配额页面：配置表单 + 定制限额 + 用量明细 */

async function fetchAiAgentQuota(shouldRender = true) {
  const res = await fetch("/api/aiagent-quota", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取 AI 配额失败");
  state.aiagentQuota = data;
  if (shouldRender) {
    renderAiAgentQuota();
  }
}

function quotaFmt(value) {
  if (value === undefined || value === null) return "0";
  if (value < 0) return "不限";
  return Number(value).toLocaleString("zh-CN");
}

function quotaPercent(used, limit) {
  if (!limit || limit <= 0) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

function renderAiAgentQuota() {
  const data = state.aiagentQuota;
  if (!data) return;
  const cfg = data.config || {};

  $("#quotaEnabled").checked = cfg.enabled === true;
  $("#quotaCountBackground").checked = cfg.count_background !== false;
  $("#quotaExemptUsers").value = (cfg.exempt_user_ids || []).join("\n");
  $("#quotaExemptGroups").value = (cfg.exempt_group_ids || []).join("\n");

  const user = cfg.default_user || {};
  const group = cfg.default_group || {};
  $("#quotaDefaultUserDaily").value = user.daily ?? 100;
  $("#quotaDefaultUserHourly").value = user.hourly ?? 10;
  $("#quotaDefaultGroupDaily").value = group.daily ?? 300;
  $("#quotaDefaultGroupHourly").value = group.hourly ?? 30;

  renderQuotaPermissions(data.permissions || {});
  renderQuotaOverrides(cfg);
  renderQuotaUsageTable(data.scopes || []);
}

function renderQuotaPermissions(permissions) {
  const whitelist = permissions.whitelist || {};
  const blacklist = permissions.blacklist || {};
  // 新配置用 user_enable/group_enable；旧配置只有 enable 时按整体开关回退显示
  const wlEnable = whitelist.enable === true;
  const blEnable = blacklist.enable === true;
  $("#quotaWhitelistUserEnabled").checked = whitelist.user_enable !== undefined ? whitelist.user_enable === true : wlEnable;
  $("#quotaWhitelistGroupEnabled").checked = whitelist.group_enable !== undefined ? whitelist.group_enable === true : wlEnable;
  $("#quotaWhitelistUsers").value = joinIds(whitelist.user);
  $("#quotaWhitelistGroups").value = joinIds(whitelist.group);
  $("#quotaBlacklistUserEnabled").checked = blacklist.user_enable !== undefined ? blacklist.user_enable === true : blEnable;
  $("#quotaBlacklistGroupEnabled").checked = blacklist.group_enable !== undefined ? blacklist.group_enable === true : blEnable;
  $("#quotaBlacklistUsers").value = joinIds(blacklist.user);
  $("#quotaBlacklistGroups").value = joinIds(blacklist.group);
}

function renderQuotaOverrides(cfg) {
  const list = $("#quotaOverrideList");
  list.replaceChildren();
  const rows = [];
  const userOv = cfg.user_overrides || {};
  const groupOv = cfg.group_overrides || {};
  for (const id of Object.keys(userOv)) {
    rows.push({ kind: "user", id, ...(userOv[id] || {}) });
  }
  for (const id of Object.keys(groupOv)) {
    rows.push({ kind: "group", id, ...(groupOv[id] || {}) });
  }
  if (!rows.length) {
    list.classList.add("empty");
    list.textContent = "暂无定制限额，所有用户/群使用默认额度。";
    return;
  }
  list.classList.remove("empty");
  for (const row of rows) {
    list.append(buildQuotaOverrideRow(row.kind, row.id, row.daily, row.hourly));
  }
}

function buildQuotaOverrideRow(kind, id, daily, hourly) {
  const row = document.createElement("div");
  row.className = "quota-override-row";
  row.innerHTML = `
    <select class="quota-ov-kind">
      <option value="user"${kind === "user" ? " selected" : ""}>用户</option>
      <option value="group"${kind === "group" ? " selected" : ""}>群</option>
    </select>
    <input class="quota-ov-id" type="text" placeholder="QQ 号 / 群号" value="${escapeHtml(id)}">
    <input class="quota-ov-daily" type="number" min="0" step="1" value="${Number(daily) || 0}" title="每日次数，0 = 不限额">
    <input class="quota-ov-hourly" type="number" min="0" step="1" value="${Number(hourly) || 0}" title="每小时次数，0 = 不限额">
    <button type="button" class="ghost quota-ov-remove">删除</button>`;
  row.querySelector(".quota-ov-remove").addEventListener("click", () => {
    row.remove();
    if (!document.querySelectorAll(".quota-override-row").length) {
      const list = $("#quotaOverrideList");
      list.classList.add("empty");
      list.textContent = "暂无定制限额，所有用户/群使用默认额度。";
    }
  });
  return row;
}

function collectQuotaOverrides() {
  const user = {};
  const group = {};
  for (const row of document.querySelectorAll(".quota-override-row")) {
    const kind = row.querySelector(".quota-ov-kind").value;
    const id = row.querySelector(".quota-ov-id").value.trim();
    if (!id) continue;
    const daily = Math.max(0, Number(row.querySelector(".quota-ov-daily").value) || 0);
    const hourly = Math.max(0, Number(row.querySelector(".quota-ov-hourly").value) || 0);
    (kind === "user" ? user : group)[id] = { daily, hourly };
  }
  return { user_overrides: user, group_overrides: group };
}

function renderQuotaUsageTable(scopes) {
  const tbody = $("#quotaUsageTableBody");
  const summary = $("#quotaUsageSummary");
  tbody.replaceChildren();

  const tab = state.quotaTab || "group";
  const list = (scopes || []).filter((s) => s.kind === tab);
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">暂无用量记录（产生 AI 对话后才会出现）</td></tr>';
    if (summary) summary.textContent = "暂无记录";
    return;
  }

  list.sort((a, b) => (b.daily?.used || 0) - (a.daily?.used || 0));
  for (const scope of list) {
    tbody.append(buildQuotaUsageRow(scope));
  }

  const totalUsed = list.reduce((sum, s) => sum + (s.daily?.used || 0), 0);
  const totalLimit = list.reduce((sum, s) => sum + (s.daily?.limit || 0), 0);
  if (summary) {
    summary.textContent = `${list.length} 个${tab === "group" ? "群" : "用户"}，今日合计 ${quotaFmt(totalUsed)} 次对话${totalLimit ? " / 限额 " + quotaFmt(totalLimit) : ""}`;
  }
}

function buildQuotaUsageRow(scope) {
  const tr = document.createElement("tr");
  const daily = scope.daily || {};
  const hourly = scope.hourly || {};
  const dailyLimit = daily.limit || 0;
  const pct = quotaPercent(daily.used || 0, dailyLimit);
  const full = dailyLimit > 0 && daily.used >= dailyLimit;

  tr.innerHTML = `
    <td>${escapeHtml(scope.id)}${scope.exempt ? '<span class="badge-exempt">豁免</span>' : ""}</td>
    <td>
      <div class="quota-cell">
        <span>${quotaFmt(daily.used)} / ${quotaFmt(daily.limit)}</span>
        <div class="quota-bar"><div class="quota-bar-fill${full ? " is-full" : ""}" style="width:${pct}%"></div></div>
      </div>
    </td>
    <td>${quotaFmt(hourly.used)} / ${quotaFmt(hourly.limit)}</td>
    <td>${quotaFmt(daily.remaining)}</td>
    <td>每日 ${escapeHtml(daily.resets_at || "-")}<br>每小时 ${escapeHtml(hourly.resets_at || "-")}</td>
    <td><button type="button" class="ghost quota-reset-one" data-scope="${escapeHtml(scope.scope)}">重置</button></td>`;
  tr.querySelector(".quota-reset-one").addEventListener("click", () => {
    resetQuotaScope(scope.scope).catch((err) => showToast(err.message, true));
  });
  return tr;
}

async function saveAiAgentQuota(event) {
  event.preventDefault();
  const overrides = collectQuotaOverrides();
  const payload = {
    quota: {
      enabled: $("#quotaEnabled").checked,
      default_user: {
        daily: Number($("#quotaDefaultUserDaily").value) || 0,
        hourly: Number($("#quotaDefaultUserHourly").value) || 0,
      },
      default_group: {
        daily: Number($("#quotaDefaultGroupDaily").value) || 0,
        hourly: Number($("#quotaDefaultGroupHourly").value) || 0,
      },
      user_overrides: overrides.user_overrides,
      group_overrides: overrides.group_overrides,
      exempt_user_ids: splitIds($("#quotaExemptUsers").value),
      exempt_group_ids: splitIds($("#quotaExemptGroups").value),
      count_background: $("#quotaCountBackground").checked,
    },
    permissions: buildQuotaPermissions(),
  };

  const button = $("#quotaConfigForm button[type='submit']");
  button.disabled = true;
  try {
    const res = await fetch("/api/aiagent-quota", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readJsonResponse(res, "保存配额设置失败");
    state.aiagentQuota = data;
    renderAiAgentQuota();
    showToast(data.message || "配额设置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function buildQuotaPermissions() {
  const wlUser = $("#quotaWhitelistUserEnabled").checked;
  const wlGroup = $("#quotaWhitelistGroupEnabled").checked;
  const blUser = $("#quotaBlacklistUserEnabled").checked;
  const blGroup = $("#quotaBlacklistGroupEnabled").checked;
  return {
    whitelist: {
      enable: wlUser || wlGroup,
      user_enable: wlUser,
      group_enable: wlGroup,
      user: splitIds($("#quotaWhitelistUsers").value),
      group: splitIds($("#quotaWhitelistGroups").value),
    },
    blacklist: {
      enable: blUser || blGroup,
      user_enable: blUser,
      group_enable: blGroup,
      user: splitIds($("#quotaBlacklistUsers").value),
      group: splitIds($("#quotaBlacklistGroups").value),
    },
  };
}

async function resetQuotaScope(scope) {
  if (!confirm(`确定重置 ${scope} 的全部用量？`)) return;
  const res = await fetch("/api/aiagent-quota/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  const data = await readJsonResponse(res, "重置用量失败");
  await fetchAiAgentQuota();
  showToast(data.message || "已重置。");
}

async function resetAllQuota() {
  if (!confirm("确定重置所有群/用户的用量？此操作不可撤销。")) return;
  const res = await fetch("/api/aiagent-quota/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "" }),
  });
  const data = await readJsonResponse(res, "重置用量失败");
  await fetchAiAgentQuota();
  showToast(data.message || "已重置全部用量。");
}

function addQuotaOverrideRow() {
  const list = $("#quotaOverrideList");
  if (list.classList.contains("empty")) {
    list.classList.remove("empty");
    list.replaceChildren();
  }
  list.append(buildQuotaOverrideRow("user", "", 0, 0));
}

function switchQuotaTab(btn) {
  state.quotaTab = btn.dataset.quotaTab;
  for (const other of document.querySelectorAll("[data-quota-tab]")) {
    other.classList.toggle("is-active", other === btn);
  }
  renderQuotaUsageTable((state.aiagentQuota || {}).scopes || []);
}
