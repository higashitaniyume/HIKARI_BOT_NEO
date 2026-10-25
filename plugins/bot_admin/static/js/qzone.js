const QZONE_DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const QZONE_DAY_ALIASES = {
  周一: 0, 星期一: 0, mon: 0, monday: 0, "0": 0,
  周二: 1, 星期二: 1, tue: 1, tuesday: 1, "1": 1,
  周三: 2, 星期三: 2, wed: 2, wednesday: 2, "2": 2,
  周四: 3, 星期四: 3, thu: 3, thursday: 3, "3": 3,
  周五: 4, 星期五: 4, fri: 4, friday: 4, "4": 4,
  周六: 5, 星期六: 5, sat: 5, saturday: 5, "5": 5,
  周日: 6, 周天: 6, 星期日: 6, 星期天: 6, sun: 6, sunday: 6, "6": 6,
};

function qzoneState() {
  return state.qzoneState || {};
}

function renderQzonePage() {
  const data = qzoneState();
  const cfg = data.config || {};
  const schedule = cfg.schedule || {};
  const ai = schedule.ai || {};

  $("#qzoneEnabled").checked = cfg.enabled !== false;
  $("#qzoneDefaultVisible").value = cfg.default_visible || "public";
  $("#qzoneMaxLength").value = cfg.max_length || 1000;
  $("#qzoneFeedCount").value = cfg.feed_count || 8;

  $("#qzoneScheduleEnabled").checked = schedule.enabled === true;
  $("#qzoneAiEnabled").checked = ai.enabled !== false;
  $("#qzoneAiMaxChars").value = ai.max_chars || 120;
  $("#qzoneGraceSeconds").value = schedule.grace_seconds || 1800;
  $("#qzoneExtraPrompt").value = ai.extra_prompt || "";
  $("#qzoneFallbackTexts").value = (schedule.fallback_texts || []).join("\n");

  renderQzoneJobs(schedule.jobs || []);
  renderQzoneScheduleSummary(data);
  renderQzonePersona(data.persona || {});
  renderQzoneCommentReply(cfg.comment_reply || {}, data.comment_reply_status || {});
}

function renderQzoneCommentReply(section, status) {
  const enabled = section.enabled === true;
  $("#qzoneCommentEnabled").checked = enabled;
  $("#qzoneCommentInterval").value = section.interval_minutes ?? 60;
  $("#qzoneCommentPosts").value = section.posts ?? 3;
  $("#qzoneCommentPerRun").value = section.per_run_limit ?? 2;
  $("#qzoneCommentDaily").value = section.daily_limit ?? 20;
  $("#qzoneCommentMode").value = section.mode === "template" ? "template" : "ai";
  $("#qzoneCommentMaxChars").value = (section.ai || {}).max_chars ?? 60;
  $("#qzoneCommentTemplate").value = section.template || "";
  $("#qzoneCommentExtraPrompt").value = (section.ai || {}).extra_prompt || "";

  const interval = Number(section.interval_minutes || 60);
  const summary = $("#qzoneCommentSummary");
  if (summary) {
    summary.textContent = `每 ${interval} 分钟检查最近 ${section.posts ?? 3} 条说说；` +
      "自己没回过的评论会按「" + (section.mode === "template" ? "固定模板" : "AI 人格") + "」回一句，" +
      "首次开启只做基线、不补回历史评论。";
  }

  const lines = [];
  lines.push(enabled ? "已启用" : "已关闭");
  if (status.baseline_done) {
    lines.push(`基线已完成，已记录 ${status.seen ?? 0} 条评论`);
  } else {
    lines.push("还没做基线（下次检查会先记录现有评论，不回复）");
  }
  lines.push(`今天已回 ${status.replied_today ?? 0} / ${status.daily_limit ?? 20} 条`);
  if (status.last_run) lines.push(`上次检查 ${status.last_run}`);
  setQzoneStatus("#qzoneCommentStatus", lines.join(" · "));
}

async function checkQzoneComments() {
  const button = $("#qzoneCheckCommentsBtn");
  button.disabled = true;
  setQzoneStatus("#qzoneCommentStatus", "⏳ 正在检查评论...");
  try {
    const res = await fetch("/api/qzone-check-comments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await readJsonResponse(res, "检查评论失败");
    const result = data.result || {};
    if (result.skipped) {
      setQzoneStatus("#qzoneCommentStatus", "评论自动回复当前是关闭的。", true);
    } else if (result.baseline !== undefined) {
      setQzoneStatus("#qzoneCommentStatus", `✅ 基线完成：记录 ${result.baseline} 条历史评论，本次没有回复。`);
    } else {
      setQzoneStatus(
        "#qzoneCommentStatus",
        `✅ 检查完成：回复 ${result.replied ?? 0} 条，待回复 ${result.pending ?? 0} 条，失败 ${result.failed ?? 0} 条。`,
      );
    }
    await fetchQzoneConfig(false);
    renderQzoneCommentReply(
      (state.qzoneState?.config || {}).comment_reply || {},
      state.qzoneState?.comment_reply_status || {},
    );
    renderQzoneScheduleSummary(state.qzoneState || {});
  } catch (err) {
    setQzoneStatus("#qzoneCommentStatus", `❌ ${err.message}`, true);
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderQzoneScheduleSummary(data) {
  const status = data.status || {};
  const jobs = Array.isArray(status.jobs) ? status.jobs : [];
  const target = $("#qzoneScheduleSummary");
  if (!target) return;
  if (!jobs.length) {
    target.textContent = "还没有定时任务";
    return;
  }
  const parts = jobs.map((job) => {
    const last = job.last_date ? `，上次 ${job.last_date}` : "";
    return `${job.id} ${job.time}（下次 ${job.next_run}${last}）`;
  });
  target.textContent = `${status.enabled ? "已开启" : "已关闭"} · ${parts.join(" · ")}`;
}

function renderQzonePersona(persona) {
  const target = $("#qzonePersonaSummary");
  if (!target) return;
  const skillPath = persona.skill_path || "未配置（使用 AI 配置的 fallback 人格）";
  const chars = persona.chars ? `${persona.chars} 字` : "0 字";
  const model = persona.model || "未配置";
  target.textContent = `人格：${skillPath}（${chars}）· 模型：${model}`;
}

function renderQzoneJobs(jobs) {
  const list = $("#qzoneJobList");
  if (!list) return;
  list.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("p");
    empty.className = "qzone-empty";
    empty.textContent = "还没有任务，点「添加任务」加一个。";
    list.append(empty);
    return;
  }
  jobs.forEach((job, index) => list.append(buildQzoneJobCard(job, index)));
}

function buildQzoneJobCard(job, index) {
  const card = document.createElement("div");
  card.className = "qzone-job";
  card.innerHTML = `
    <div class="qzone-job-head">
      <strong>任务 ${index + 1}</strong>
      <button type="button" class="danger-button" data-qzone-remove-job="1">删除任务</button>
    </div>
    <div class="qzone-job-grid">
      <label><span>任务 id</span><input data-qzone-field="id" maxlength="40" placeholder="morning"></label>
      <label><span>时间</span><input type="time" data-qzone-field="time"></label>
      <label><span>星期（留空=每天）</span><input data-qzone-field="days" placeholder="周一,周三"></label>
      <label><span>可见范围</span><select data-qzone-field="visible">
        <option value="public">公开</option>
        <option value="friends">好友可见</option>
        <option value="private">仅自己</option>
      </select></label>
      <label class="checkbox-field qzone-job-enabled">
        <input type="checkbox" data-qzone-field="enabled">
        <span>启用这个任务</span>
      </label>
      <label class="wide-field"><span>这条想写什么</span><input data-qzone-field="prompt" maxlength="200" placeholder="刚睡醒，写一句早安或今天想做的小事"></label>
    </div>`;

  card.querySelector('[data-qzone-field="id"]').value = job.id || "";
  card.querySelector('[data-qzone-field="time"]').value = job.time || "08:30";
  card.querySelector('[data-qzone-field="days"]').value = qzoneDaysToText(job.days || []);
  card.querySelector('[data-qzone-field="visible"]').value = job.visible || "public";
  card.querySelector('[data-qzone-field="enabled"]').checked = job.enabled !== false;
  card.querySelector('[data-qzone-field="prompt"]').value = job.prompt || "";
  card.querySelector("[data-qzone-remove-job]").addEventListener("click", () => card.remove());
  return card;
}

function addQzoneJob() {
  const jobs = Array.from(document.querySelectorAll("#qzoneJobList .qzone-job"));
  const used = new Set(jobs.map((card) => card.querySelector('[data-qzone-field="id"]')?.value?.trim()));
  let index = jobs.length + 1;
  let id = `job_${index}`;
  while (used.has(id)) {
    index += 1;
    id = `job_${index}`;
  }
  const card = buildQzoneJobCard(
    { id, enabled: true, time: "09:00", days: [], visible: "public", prompt: "" },
    jobs.length,
  );
  const list = $("#qzoneJobList");
  const empty = list.querySelector(".qzone-empty");
  if (empty) empty.remove();
  list.append(card);
}

function qzoneDaysToText(days) {
  return (Array.isArray(days) ? days : [])
    .map((day) => QZONE_DAY_LABELS[Number(day)])
    .filter(Boolean)
    .join(",");
}

function qzoneTextToDays(value) {
  const seen = new Set();
  const result = [];
  String(value || "")
    .split(/[\s,，、;；]+/)
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean)
    .forEach((token) => {
      const day = QZONE_DAY_ALIASES[token];
      if (day === undefined || seen.has(day)) return;
      seen.add(day);
      result.push(day);
    });
  return result.sort((a, b) => a - b);
}

function collectQzoneJobs() {
  return Array.from(document.querySelectorAll("#qzoneJobList .qzone-job")).map((card) => {
    const field = (name) => card.querySelector(`[data-qzone-field="${name}"]`);
    return {
      id: String(field("id")?.value || "").trim(),
      enabled: Boolean(field("enabled")?.checked),
      time: String(field("time")?.value || "").trim(),
      days: qzoneTextToDays(field("days")?.value || ""),
      visible: String(field("visible")?.value || "public"),
      prompt: String(field("prompt")?.value || "").trim(),
    };
  });
}

async function saveQzoneConfig() {
  const button = $("#qzoneSaveConfigBtn");
  button.disabled = true;
  try {
    const payload = {
      enabled: $("#qzoneEnabled").checked,
      default_visible: $("#qzoneDefaultVisible").value,
      max_length: Number($("#qzoneMaxLength").value) || 1000,
      feed_count: Number($("#qzoneFeedCount").value) || 8,
      schedule: {
        enabled: $("#qzoneScheduleEnabled").checked,
        grace_seconds: Number($("#qzoneGraceSeconds").value) || 1800,
        ai: {
          enabled: $("#qzoneAiEnabled").checked,
          max_chars: Number($("#qzoneAiMaxChars").value) || 120,
          extra_prompt: $("#qzoneExtraPrompt").value.trim(),
        },
        fallback_texts: $("#qzoneFallbackTexts")
          .value.split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        jobs: collectQzoneJobs(),
      },
      comment_reply: {
        enabled: $("#qzoneCommentEnabled").checked,
        interval_minutes: Number($("#qzoneCommentInterval").value) || 60,
        posts: Number($("#qzoneCommentPosts").value) || 3,
        per_run_limit: Number($("#qzoneCommentPerRun").value) || 2,
        daily_limit: Number($("#qzoneCommentDaily").value) || 0,
        mode: $("#qzoneCommentMode").value,
        template: $("#qzoneCommentTemplate").value.trim(),
        ai: {
          max_chars: Number($("#qzoneCommentMaxChars").value) || 60,
          extra_prompt: $("#qzoneCommentExtraPrompt").value.trim(),
        },
      },
    };
    const res = await fetch("/api/qzone-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readJsonResponse(res, "保存空间设置失败");
    state.qzoneState = data;
    renderQzonePage();
    showToast(data.message || "空间设置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function setQzoneStatus(selector, text, isError = false) {
  const target = $(selector);
  if (!target) return;
  target.textContent = text || "";
  target.classList.toggle("is-error", Boolean(isError));
}

async function generateQzoneDraft() {
  const button = $("#qzoneGenerateBtn");
  button.disabled = true;
  setQzoneStatus("#qzoneDraftStatus", "⏳ 正在让 AI 写...");
  try {
    const res = await fetch("/api/qzone-generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: $("#qzoneDraftPrompt").value.trim() }),
    });
    const data = await readJsonResponse(res, "生成草稿失败");
    const result = data.result || {};
    $("#qzoneDraftText").value = result.text || "";
    const sourceLabel = result.source === "ai" ? "AI 生成" : "备用文案（AI 没写成）";
    setQzoneStatus("#qzoneDraftStatus", `✅ ${sourceLabel} · ${(result.text || "").length} 字`);
    if (result.persona) renderQzonePersona(result.persona);
  } catch (err) {
    setQzoneStatus("#qzoneDraftStatus", `❌ ${err.message}`, true);
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function publishQzoneDraft() {
  const text = $("#qzoneDraftText").value.trim();
  if (!text) {
    showToast("说说内容不能为空。", true);
    return;
  }
  const button = $("#qzonePublishBtn");
  button.disabled = true;
  setQzoneStatus("#qzonePublishStatus", "⏳ 正在发布...");
  try {
    const res = await fetch("/api/qzone-publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, visible: $("#qzonePublishVisible").value }),
    });
    const data = await readJsonResponse(res, "发布失败");
    const result = data.result || {};
    setQzoneStatus(
      "#qzonePublishStatus",
      `✅ 已发布（${result.visible_label || "公开"}）：${result.tid || "-"}`,
    );
    showToast(data.message || "已发布到 QQ 空间。");
    await fetchQzoneConfig(false);
    renderQzoneScheduleSummary(state.qzoneState || {});
  } catch (err) {
    setQzoneStatus("#qzonePublishStatus", `❌ ${err.message}`, true);
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}
