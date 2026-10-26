// 「终端」页：群 / 私聊专属 Linux 账号清单、调用历史与单次输出。

const TERMINAL_ACTION_LABELS = {
  exec: "执行命令",
  root_exec: "root 命令",
  write: "写文件",
  edit: "改文件",
  provision: "开通账号",
};

function terminalTime(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "—";
  const date = new Date(value * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function terminalCell(text, className) {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function terminalEmptyRow(tbody, columns, text) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = columns;
  cell.className = "empty-state";
  cell.textContent = text;
  row.appendChild(cell);
  tbody.replaceChildren(row);
}

function terminalSessionCell(row) {
  const cell = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "badge terminal-kind-badge";
  badge.textContent = row.kind === "group" ? "群" : row.kind === "private" ? "私聊" : "未知";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "link-button terminal-session-filter";
  button.textContent = String(row.id || "—");
  button.title = "只看该会话的记录";
  button.addEventListener("click", () => {
    state.terminalQuery = String(row.id || "");
    const input = $("#terminalSearch");
    if (input) input.value = state.terminalQuery;
    fetchTerminalOverview().catch((err) => showToast(err.message, true));
  });
  cell.appendChild(badge);
  cell.appendChild(button);
  return cell;
}

function terminalTruncated(text, limit = 120) {
  const value = String(text || "");
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

function renderTerminalOverview(data) {
  const stats = data.stats || {};
  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  const records = Array.isArray(data.records) ? data.records : [];

  $("#terminalUserCount").textContent = String(stats.users || 0);
  $("#terminalGroupCount").textContent = String(stats.groups || 0);
  $("#terminalPrivateCount").textContent = String(stats.privates || 0);
  $("#terminalTodayCount").textContent = String(stats.today_calls || 0);
  $("#terminalTotalCount").textContent = String(stats.total_calls || 0);
  $("#terminalActiveCount").textContent = String(stats.active_24h || 0);

  $("#terminalSummaryText").textContent = [
    `目标 ${stats.target || "-"}`,
    stats.enabled ? "终端已启用" : "终端未启用",
    stats.private_enabled ? "私聊已开放" : "私聊未开放",
    stats.root_enabled ? "root 模式已开" : "root 模式未开",
    `黑名单 ${stats.blocked_groups || 0} 个群`,
    `更新于 ${data.generated_at || "-"}`,
  ].join(" · ");

  const errorBox = $("#terminalError");
  if (errorBox) errorBox.hidden = true;

  const userTable = $("#terminalUserTable");
  if (!sessions.length) {
    terminalEmptyRow(userTable, 7, "暂无终端用户（群里第一次用终端后会自动开通）");
  } else {
    const rows = sessions.map((session) => {
      const row = document.createElement("tr");
      row.appendChild(terminalSessionCell(session));
      row.appendChild(terminalCell(session.user || "—", "terminal-mono"));
      row.appendChild(terminalCell(session.home || "—", "terminal-mono"));
      const cwd = terminalCell(session.cwd || "—", "terminal-mono terminal-ellipsis");
      cwd.title = session.cwd || "";
      row.appendChild(cwd);
      row.appendChild(terminalCell(terminalTime(session.created_at)));
      row.appendChild(terminalCell(terminalTime(session.last_used)));
      row.appendChild(terminalCell(String(session.today_calls || 0)));
      return row;
    });
    userTable.replaceChildren(...rows);
  }

  $("#terminalRecordSummary").textContent = `最近 ${records.length} 条记录 / 累计 ${stats.total_calls || 0} 次 · 更新于 ${data.generated_at || "-"}`;

  const recordTable = $("#terminalRecordTable");
  if (!records.length) {
    terminalEmptyRow(recordTable, 7, "暂无调用记录");
    return;
  }

  const rows = records.map((record) => {
    const row = document.createElement("tr");
    row.appendChild(terminalCell(record.ts || "—"));

    const sessionCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge terminal-kind-badge";
    badge.textContent = record.kind === "group" ? "群" : record.kind === "private" ? "私聊" : "未知";
    sessionCell.appendChild(badge);
    sessionCell.appendChild(document.createTextNode(record.id || "—"));
    row.appendChild(sessionCell);

    row.appendChild(terminalCell(record.user || "—", "terminal-mono"));

    const actionCell = document.createElement("td");
    const actionBadge = document.createElement("span");
    actionBadge.className = `terminal-action terminal-action-${record.action || "other"}`;
    actionBadge.textContent = TERMINAL_ACTION_LABELS[record.action] || record.action || "—";
    actionCell.appendChild(actionBadge);
    row.appendChild(actionCell);

    const content = record.command || record.path || "—";
    const contentCell = terminalCell(terminalTruncated(content), "terminal-mono terminal-ellipsis");
    contentCell.title = content;
    row.appendChild(contentCell);

    let resultText = "—";
    if (record.action === "exec" || record.action === "root_exec") {
      const code = record.exit_code;
      resultText = `exit ${code ?? "?"}`;
      if (record.duration_ms != null) resultText += ` · ${record.duration_ms}ms`;
      if (record.truncated) resultText += " · 截断";
    } else if (record.action === "write") {
      resultText = `${record.bytes ?? 0} 字节`;
    } else if (record.action === "edit") {
      resultText = `${record.replacements ?? 0} 处替换`;
    }
    const resultCell = terminalCell(resultText);
    if (record.action === "exec" || record.action === "root_exec") {
      resultCell.classList.add(record.exit_code === 0 ? "terminal-exit-ok" : "terminal-exit-bad");
    }
    row.appendChild(resultCell);

    const actionTd = document.createElement("td");
    if (record.log_name) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost terminal-log-btn";
      button.textContent = "查看输出";
      button.addEventListener("click", () => {
        openTerminalLog(record.session, record.log_name).catch((err) => showToast(err.message, true));
      });
      actionTd.appendChild(button);
    }
    row.appendChild(actionTd);
    return row;
  });
  recordTable.replaceChildren(...rows);
}

async function openTerminalLog(session, name) {
  const dialog = $("#terminalLogDialog");
  const content = $("#terminalLogContent");
  const title = $("#terminalLogTitle");
  const meta = $("#terminalLogMeta");
  if (!dialog || !content) return;

  title.textContent = `${session} · ${name}`;
  meta.textContent = "加载中…";
  content.textContent = "加载中…";
  if (typeof dialog.showModal === "function") dialog.showModal();

  try {
    const data = await fetchTerminalLog(session, name);
    content.textContent = data.content || "（空输出）";
    const parts = [`${formatBytes(data.size || 0)}`];
    if (data.truncated) parts.push("只显示尾部内容");
    meta.textContent = parts.join(" · ");
  } catch (err) {
    content.textContent = err.message;
    meta.textContent = "读取失败";
  }
}

let terminalSearchTimer = null;

function onTerminalSearchInput(event) {
  state.terminalQuery = String(event.currentTarget.value || "").trim();
  window.clearTimeout(terminalSearchTimer);
  terminalSearchTimer = window.setTimeout(() => {
    fetchTerminalOverview(true).catch((err) => showToast(err.message, true));
  }, 350);
}
