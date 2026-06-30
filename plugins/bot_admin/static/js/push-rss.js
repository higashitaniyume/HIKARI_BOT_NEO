function pushJobs() {
  if (!Array.isArray(state.pushConfig.jobs)) {
    state.pushConfig.jobs = [];
  }
  return state.pushConfig.jobs;
}

function currentPushJob() {
  return pushJobs().find((job) => job.id === state.selectedPushJobId) || null;
}

function sourceLabel(sourceName) {
  const source = (state.pushSources || []).find((item) => item.name === sourceName);
  return source?.description ? `${sourceName} - ${source.description}` : sourceName;
}

function parseJsonObject(value, fallback = {}) {
  const text = String(value || "").trim();
  if (!text) return fallback;
  const parsed = JSON.parse(text);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("消息源参数必须是 JSON 对象。");
  }
  return parsed;
}

function splitTextList(value) {
  return String(value || "")
    .split(/[\s,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function prettyJson(value) {
  return JSON.stringify(value && typeof value === "object" ? value : {}, null, 2);
}

function newPushJobId() {
  const used = new Set(pushJobs().map((job) => job.id));
  let index = pushJobs().length + 1;
  let id = `push_job_${index}`;
  while (used.has(id)) {
    index += 1;
    id = `push_job_${index}`;
  }
  return id;
}

function buildDefaultPushJob() {
  const sourceName = state.pushSources[0]?.name || "static_text";
  const options = sourceName === "static_text" ? { text: "这是一条定时推送。" } : {};
  return {
    id: newPushJobId(),
    enabled: false,
    trigger: "schedule",
    source: sourceName,
    time: "09:00",
    times: [],
    timezone: "Asia/Shanghai",
    days: [],
    late_grace_seconds: 7200,
    dedupe: "daily",
    targets: {
      group_ids: [],
      private_user_ids: [],
    },
    source_options: options,
  };
}

function collectPushFormToState() {
  const cfg = state.pushConfig || {};
  cfg.enabled = $("#pushEnabled").checked;
  cfg.startup_delay_seconds = Number($("#pushStartupDelay").value || 15);
  cfg.check_interval_seconds = Number($("#pushCheckInterval").value || 60);
  cfg.send_retry_attempts = Number($("#pushRetryAttempts").value || 2);
  cfg.send_retry_delay_seconds = Number($("#pushRetryDelay").value || 2);
  cfg.jobs = pushJobs();

  const job = currentPushJob();
  if (job && !$("#pushConfigForm").hidden) {
    job.enabled = $("#pushJobEnabled").checked;
    job.id = $("#pushJobId").value.trim();
    state.selectedPushJobId = job.id;
    job.source = $("#pushJobSource").value.trim();
    job.trigger = $("#pushJobTrigger").value || "schedule";
    job.time = $("#pushJobTime").value.trim() || "09:00";
    job.times = splitTextList($("#pushJobTimes").value);
    job.timezone = $("#pushJobTimezone").value.trim() || "Asia/Shanghai";
    job.days = splitTextList($("#pushJobDays").value);
    job.late_grace_seconds = Number($("#pushJobLateGrace").value || 7200);
    job.dedupe = $("#pushJobDedupe").value || "daily";
    job.targets = {
      group_ids: splitIds($("#pushJobGroups").value),
      private_user_ids: splitIds($("#pushJobPrivates").value),
    };
    job.source_options = parseJsonObject($("#pushJobOptions").value);
  }

  state.pushConfig = cfg;
  return cfg;
}

function renderPushConfig() {
  const cfg = state.pushConfig || {};
  $("#pushEnabled").checked = cfg.enabled !== false;
  $("#pushStartupDelay").value = cfg.startup_delay_seconds ?? 15;
  $("#pushCheckInterval").value = cfg.check_interval_seconds ?? 60;
  $("#pushRetryAttempts").value = cfg.send_retry_attempts ?? 2;
  $("#pushRetryDelay").value = cfg.send_retry_delay_seconds ?? 2.0;
  renderPushJobs();
  renderPushJobDetail();
}

function renderPushJobs() {
  const list = $("#pushJobList");
  const jobs = pushJobs();
  list.replaceChildren();
  if (!jobs.length) {
    list.className = "ops-list empty";
    list.textContent = "暂无推送任务";
    return;
  }

  list.className = "ops-list";
  for (const job of jobs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ops-list-item";
    button.classList.toggle("is-active", job.id === state.selectedPushJobId);
    button.addEventListener("click", () => {
      try {
        collectPushFormToState();
      } catch (err) {
        showToast(err.message, true);
        return;
      }
      state.selectedPushJobId = job.id;
      renderPushConfig();
    });

    const title = document.createElement("span");
    title.className = "ops-list-title";
    title.textContent = job.id || "<未命名任务>";
    const meta = document.createElement("span");
    meta.className = "ops-list-meta";
    const targets = (job.targets?.group_ids?.length || 0) + (job.targets?.private_user_ids?.length || 0);
    meta.textContent = `${job.enabled === false ? "关闭" : "开启"} / ${job.trigger || "schedule"} / ${job.source || "未选源"} / ${job.time || "09:00"} / ${targets} 个目标`;
    button.append(title, meta);
    list.append(button);
  }
}

function renderPushJobDetail() {
  const job = currentPushJob();
  $("#pushConfigForm").hidden = false;
  $("#pushRunJobBtn").disabled = !job;
  $("#pushDeleteJobBtn").disabled = !job;
  if (!job) {
    $("#pushEditorTitle").textContent = "全局设置";
    $("#pushEditorMeta").textContent = "添加任务后可编辑具体推送内容";
    $("#pushJobEnabled").checked = false;
    $("#pushJobId").value = "";
    $("#pushJobSource").replaceChildren(option("", "暂无任务"));
    $("#pushJobTrigger").value = "schedule";
    $("#pushJobTime").value = "09:00";
    $("#pushJobTimes").value = "";
    $("#pushJobTimezone").value = "Asia/Shanghai";
    $("#pushJobDays").value = "";
    $("#pushJobLateGrace").value = 7200;
    $("#pushJobDedupe").value = "daily";
    $("#pushJobGroups").value = "";
    $("#pushJobPrivates").value = "";
    $("#pushJobOptions").value = "{}";
    $("#pushSourceSummary").textContent = "暂无选中的推送任务";
    return;
  }

  $("#pushEditorTitle").textContent = job.id || "未命名任务";
  $("#pushEditorMeta").textContent = `${job.enabled === false ? "关闭" : "开启"} / ${job.trigger || "schedule"} / ${sourceLabel(job.source || "")}`;
  $("#pushJobEnabled").checked = job.enabled !== false;
  $("#pushJobId").value = job.id || "";
  const sourceSelect = $("#pushJobSource");
  sourceSelect.replaceChildren();
  const sourceNames = new Set((state.pushSources || []).map((source) => source.name));
  if (job.source && !sourceNames.has(job.source)) {
    sourceSelect.append(option(job.source, `${job.source}（未注册）`));
  }
  for (const source of state.pushSources || []) {
    sourceSelect.append(option(source.name, sourceLabel(source.name)));
  }
  if (!sourceSelect.options.length) {
    sourceSelect.append(option(job.source || "static_text", job.source || "static_text"));
  }
  sourceSelect.value = job.source || sourceSelect.options[0]?.value || "";
  $("#pushJobTrigger").value = job.trigger || "schedule";
  $("#pushJobTime").value = job.time || "09:00";
  $("#pushJobTimes").value = Array.isArray(job.times) ? job.times.join("\n") : "";
  $("#pushJobTimezone").value = job.timezone || "Asia/Shanghai";
  $("#pushJobDays").value = Array.isArray(job.days) ? job.days.join(",") : "";
  $("#pushJobLateGrace").value = job.late_grace_seconds ?? 7200;
  $("#pushJobDedupe").value = job.dedupe || "daily";
  $("#pushJobGroups").value = joinIds(job.targets?.group_ids);
  $("#pushJobPrivates").value = joinIds(job.targets?.private_user_ids);
  $("#pushJobOptions").value = prettyJson(job.source_options);
  const source = (state.pushSources || []).find((item) => item.name === sourceSelect.value);
  $("#pushSourceSummary").textContent = source?.description || "当前消息源未提供说明";
}

function addPushJob() {
  try {
    if (Object.keys(state.pushConfig || {}).length) {
      collectPushFormToState();
    }
  } catch (err) {
    showToast(err.message, true);
    return;
  }
  const job = buildDefaultPushJob();
  pushJobs().push(job);
  state.selectedPushJobId = job.id;
  renderPushConfig();
}

function deleteSelectedPushJob() {
  const job = currentPushJob();
  if (!job) return;
  const confirmed = window.confirm(`确定删除推送任务「${job.id}」吗？`);
  if (!confirmed) return;
  state.pushConfig.jobs = pushJobs().filter((item) => item !== job);
  state.selectedPushJobId = state.pushConfig.jobs[0]?.id || "";
  renderPushConfig();
}

async function savePushConfig(event) {
  event.preventDefault();
  const button = $("#pushSaveBtn");
  button.disabled = true;
  try {
    const data = await persistPushConfig();
    showToast(data.message || "推送配置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

async function persistPushConfig() {
  const payload = collectPushFormToState();
  const res = await fetch("/api/push-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await readJsonResponse(res, "保存推送配置失败");
  state.pushConfig = data.config || {};
  state.pushSources = data.sources || state.pushSources;
  const jobs = pushJobs();
  if (!jobs.some((job) => job.id === state.selectedPushJobId)) {
    state.selectedPushJobId = jobs[0]?.id || "";
  }
  renderPushConfig();
  return data;
}

async function runSelectedPushJob() {
  const button = $("#pushRunJobBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "推送中...";
  try {
    if (!currentPushJob()) {
      throw new Error("请先选择一个推送任务。");
    }
    await persistPushConfig();
    button.disabled = true;
    const job = currentPushJob();
    if (!job) {
      throw new Error("请先选择一个推送任务。");
    }
    const res = await fetch("/api/push-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job.id }),
    });
    const data = await readJsonResponse(res, "手动触发推送失败");
    const result = data.result || {};
    const failed = Number(result.failed || 0);
    const errorHint = Array.isArray(result.errors) && result.errors.length ? `；${result.errors[0]}` : "";
    showToast(
      `${data.message || "手动推送完成。"} 成功 ${Number(result.sent || 0)}，跳过 ${Number(result.skipped || 0)}，空内容 ${Number(result.empty || 0)}，失败 ${failed}${errorHint}`,
      failed > 0,
    );
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.textContent = originalText;
    renderPushJobDetail();
  }
}

function rssSubscriptions() {
  if (!Array.isArray(state.rssConfig.subscriptions)) {
    state.rssConfig.subscriptions = [];
  }
  return state.rssConfig.subscriptions;
}

function currentRssSubscription() {
  return rssSubscriptions().find((item) => item.id === state.selectedRssSubscriptionId) || null;
}

function newRssSubscriptionId() {
  const used = new Set(rssSubscriptions().map((item) => item.id));
  let index = rssSubscriptions().length + 1;
  let id = `rss_feed_${index}`;
  while (used.has(id)) {
    index += 1;
    id = `rss_feed_${index}`;
  }
  return id;
}

function buildDefaultRssSubscription() {
  const id = newRssSubscriptionId();
  return {
    id,
    enabled: true,
    title: id,
    url: "",
    max_items: 3,
    include_summary: true,
    summary_max_chars: Number(state.rssConfig.summary_max_chars || 220),
    only_new: true,
    send_first_run: true,
  };
}

function collectRssFormToState() {
  const cfg = state.rssConfig || {};
  cfg.enabled = $("#rssEnabled").checked;
  cfg.timeout_seconds = Number($("#rssTimeout").value || 20);
  cfg.proxy = $("#rssProxy").value.trim();
  cfg.user_agent = $("#rssUserAgent").value.trim() || "HIKARI_BOT_NEO RSS Reader";
  cfg.max_items = Number($("#rssMaxItems").value || 5);
  cfg.summary_max_chars = Number($("#rssSummaryMaxChars").value || 220);
  cfg.max_message_chars = Number($("#rssMaxMessageChars").value || 3500);
  cfg.max_feed_bytes = Number($("#rssMaxFeedBytes").value || 2097152);
  cfg.max_state_entries = Number($("#rssMaxStateEntries").value || 1000);
  cfg.subscriptions = rssSubscriptions();

  const subscription = currentRssSubscription();
  if (subscription) {
    subscription.enabled = $("#rssSubscriptionEnabled").checked;
    subscription.id = $("#rssSubscriptionId").value.trim();
    state.selectedRssSubscriptionId = subscription.id;
    subscription.title = $("#rssSubscriptionTitle").value.trim() || subscription.id;
    subscription.url = $("#rssSubscriptionUrl").value.trim();
    subscription.max_items = Number($("#rssSubscriptionMaxItems").value || 3);
    subscription.include_summary = $("#rssSubscriptionIncludeSummary").checked;
    subscription.summary_max_chars = Number($("#rssSubscriptionSummaryMaxChars").value || 220);
    subscription.only_new = $("#rssSubscriptionOnlyNew").checked;
    subscription.send_first_run = $("#rssSubscriptionSendFirstRun").checked;
  }

  state.rssConfig = cfg;
  return cfg;
}

function renderRssConfig() {
  const cfg = state.rssConfig || {};
  $("#rssEnabled").checked = cfg.enabled !== false;
  $("#rssTimeout").value = cfg.timeout_seconds ?? 20;
  $("#rssProxy").value = cfg.proxy || "";
  $("#rssUserAgent").value = cfg.user_agent || "HIKARI_BOT_NEO RSS Reader";
  $("#rssMaxItems").value = cfg.max_items ?? 5;
  $("#rssSummaryMaxChars").value = cfg.summary_max_chars ?? 220;
  $("#rssMaxMessageChars").value = cfg.max_message_chars ?? 3500;
  $("#rssMaxFeedBytes").value = cfg.max_feed_bytes ?? 2097152;
  $("#rssMaxStateEntries").value = cfg.max_state_entries ?? 1000;
  renderRssSubscriptions();
  renderRssSubscriptionDetail();
}

function renderRssSubscriptions() {
  const list = $("#rssSubscriptionList");
  const subscriptions = rssSubscriptions();
  list.replaceChildren();
  if (!subscriptions.length) {
    list.className = "ops-list empty";
    list.textContent = "暂无 RSS 订阅";
    return;
  }

  list.className = "ops-list";
  for (const subscription of subscriptions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ops-list-item";
    button.classList.toggle("is-active", subscription.id === state.selectedRssSubscriptionId);
    button.addEventListener("click", () => {
      collectRssFormToState();
      state.selectedRssSubscriptionId = subscription.id;
      renderRssConfig();
    });

    const title = document.createElement("span");
    title.className = "ops-list-title";
    title.textContent = subscription.id || "<未命名订阅>";
    const meta = document.createElement("span");
    meta.className = "ops-list-meta";
    meta.textContent = `${subscription.enabled === false ? "关闭" : "开启"} / ${subscription.title || subscription.id || "RSS"} / ${subscription.max_items || 3} 条`;
    button.append(title, meta);
    list.append(button);
  }
}

function renderRssSubscriptionDetail() {
  const subscription = currentRssSubscription();
  $("#rssDeleteSubscriptionBtn").disabled = !subscription;
  if (!subscription) {
    $("#rssEditorTitle").textContent = "全局设置";
    $("#rssEditorMeta").textContent = "添加订阅后可编辑具体来源";
    $("#rssSubscriptionEnabled").checked = false;
    $("#rssSubscriptionId").value = "";
    $("#rssSubscriptionTitle").value = "";
    $("#rssSubscriptionUrl").value = "";
    $("#rssSubscriptionMaxItems").value = 3;
    $("#rssSubscriptionSummaryMaxChars").value = state.rssConfig.summary_max_chars ?? 220;
    $("#rssSubscriptionIncludeSummary").checked = true;
    $("#rssSubscriptionOnlyNew").checked = true;
    $("#rssSubscriptionSendFirstRun").checked = true;
    $("#rssSubscriptionSummary").textContent = "暂无选中的 RSS 订阅";
    return;
  }

  $("#rssEditorTitle").textContent = subscription.id || "未命名订阅";
  $("#rssEditorMeta").textContent = `${subscription.enabled === false ? "关闭" : "开启"} / ${subscription.title || subscription.id || "RSS"}`;
  $("#rssSubscriptionEnabled").checked = subscription.enabled !== false;
  $("#rssSubscriptionId").value = subscription.id || "";
  $("#rssSubscriptionTitle").value = subscription.title || "";
  $("#rssSubscriptionUrl").value = subscription.url || "";
  $("#rssSubscriptionMaxItems").value = subscription.max_items ?? 3;
  $("#rssSubscriptionSummaryMaxChars").value = subscription.summary_max_chars ?? state.rssConfig.summary_max_chars ?? 220;
  $("#rssSubscriptionIncludeSummary").checked = subscription.include_summary !== false;
  $("#rssSubscriptionOnlyNew").checked = subscription.only_new !== false;
  $("#rssSubscriptionSendFirstRun").checked = subscription.send_first_run !== false;
  $("#rssSubscriptionSummary").textContent = subscription.url || "请填写 RSS/Atom Feed URL";
}

function addRssSubscription() {
  try {
    if (Object.keys(state.rssConfig || {}).length) {
      collectRssFormToState();
    }
  } catch (err) {
    showToast(err.message, true);
    return;
  }
  const subscription = buildDefaultRssSubscription();
  rssSubscriptions().push(subscription);
  state.selectedRssSubscriptionId = subscription.id;
  renderRssConfig();
}

function deleteSelectedRssSubscription() {
  const subscription = currentRssSubscription();
  if (!subscription) return;
  const confirmed = window.confirm(`确定删除 RSS 订阅「${subscription.id}」吗？`);
  if (!confirmed) return;
  state.rssConfig.subscriptions = rssSubscriptions().filter((item) => item !== subscription);
  state.selectedRssSubscriptionId = state.rssConfig.subscriptions[0]?.id || "";
  renderRssConfig();
}

async function saveRssConfig(event) {
  event.preventDefault();
  const button = $("#rssSaveBtn");
  button.disabled = true;
  try {
    const payload = collectRssFormToState();
    const res = await fetch("/api/rss-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readJsonResponse(res, "保存 RSS 订阅设置失败");
    state.rssConfig = data.config || {};
    const subscriptions = rssSubscriptions();
    if (!subscriptions.some((item) => item.id === state.selectedRssSubscriptionId)) {
      state.selectedRssSubscriptionId = subscriptions[0]?.id || "";
    }
    renderRssConfig();
    showToast(data.message || "RSS 订阅设置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}
