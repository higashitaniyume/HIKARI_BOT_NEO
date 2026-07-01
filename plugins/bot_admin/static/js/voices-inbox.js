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

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  return `${minutes} 分钟`;
}

function formatProbePercent(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : "未知";
}

function setText(selector, value) {
  const node = $(selector);
  if (node) {
    node.textContent = value;
  }
}

function renderSystemProbe() {
  const probe = state.systemProbe || {};
  const memory = probe.memory || {};
  const disk = probe.disk || {};
  const cpu = probe.cpu || {};
  const host = probe.host || {};
  const process = probe.process || {};
  const errorNode = $("#systemProbeError");
  const loadAverage = Array.isArray(cpu.load_average) ? cpu.load_average.join(" / ") : "不可用";

  if (errorNode) {
    errorNode.hidden = !state.systemProbeError;
    errorNode.textContent = state.systemProbeError || "";
  }
  setText("#probeCapturedAt", probe.captured_at ? formatTime(probe.captured_at) : "等待刷新");
  setText("#probeHostName", host.hostname || "未知主机");
  setText("#probePlatform", host.platform || "未知系统");
  setText("#probePython", host.python || "未知");
  setText("#probeCpuPercent", formatProbePercent(cpu.percent));
  setText("#probeCpuCount", cpu.count ? `${cpu.count} 核` : "未知");
  setText("#probeLoadAverage", loadAverage);
  setText("#probeMemoryUsed", memory.used != null && memory.total != null ? `${formatBytes(memory.used)} / ${formatBytes(memory.total)}` : "未知");
  setText("#probeMemoryPercent", formatProbePercent(memory.percent));
  setText("#probeDiskUsed", disk.used != null && disk.total != null ? `${formatBytes(disk.used)} / ${formatBytes(disk.total)}` : "未知");
  setText("#probeDiskPercent", formatProbePercent(disk.percent));
  setText("#probeSystemUptime", probe.uptime_seconds != null ? formatDuration(probe.uptime_seconds) : "未知");
  setText("#probeProcessUptime", process.uptime_seconds != null ? formatDuration(process.uptime_seconds) : "未知");
  setText("#probeProcessRss", process.rss_bytes != null ? formatBytes(process.rss_bytes) : "未知");
  setText("#probeProcessThreads", process.thread_count != null ? `${process.thread_count} 个` : "未知");
  setText("#probePid", process.pid ? String(process.pid) : "未知");
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

function inboxTargetIds(itemId) {
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
    card.dataset.inboxId = item.id;

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
    moveButton.addEventListener("click", () => openPackPicker(inboxTargetIds(item.id)));

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
  renderSystemProbe();
  renderSelects();
  renderInbox();
  renderPacks();
  renderPackDetail();
  renderKeywords();
  renderVoices();
  renderVoiceKeywords();
  renderTtsConfig();
  renderAiAgentConfig();
  if (Object.keys(state.pushConfig || {}).length) {
    renderPushConfig();
  }
  if (Object.keys(state.rssConfig || {}).length) {
    renderRssConfig();
  }
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
