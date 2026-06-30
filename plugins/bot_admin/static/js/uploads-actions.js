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

