$("#keywordForm").addEventListener("submit", addKeyword);
$("#packStickerActionForm").addEventListener("submit", (event) => event.preventDefault());
$("#uploadForm").addEventListener("submit", uploadStickers);
$("#tgForm").addEventListener("submit", importTelegramStickers);
$("#voiceUploadForm").addEventListener("submit", uploadVoices);
$("#voiceKeywordForm").addEventListener("submit", addVoiceKeyword);
$("#ttsConfigForm").addEventListener("submit", saveTtsConfig);
$("#aiagentConfigForm").addEventListener("submit", saveAiAgentConfig);
$("#pushConfigForm").addEventListener("submit", savePushConfig);
$("#rssConfigForm").addEventListener("submit", saveRssConfig);
$("#accessRulesForm").addEventListener("submit", saveAccessRules);
$("#ttsVoiceForm").addEventListener("submit", saveTtsVoice);
$("#ttsVoiceEditCancel").addEventListener("click", resetTtsVoiceEdit);
$("#inboxForm").addEventListener("submit", assignInboxItems);
$("#inboxDeleteBtn").addEventListener("click", deleteInboxItems);
$("#inboxSelectAll").addEventListener("change", toggleInboxSelection);
$("#packStickerSelectAll").addEventListener("change", togglePackStickerSelection);
$("#packStickerMoveBtn").addEventListener("click", moveSelectedPackStickers);
$("#packStickerDeleteBtn").addEventListener("click", deleteSelectedPackStickers);
$("#packClearBtn").addEventListener("click", clearSelectedPackDetail);
$("#packDownloadBtn").addEventListener("click", downloadSelectedPackArchive);
$("#packPickerClose").addEventListener("click", closePackPicker);
$("#packPickerSearch").addEventListener("input", renderPackPicker);
$("#packPickerDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) {
    closePackPicker();
  }
});
$("#packPickerDialog").addEventListener("close", () => {
  state.pickerInboxIds = [];
});
$("#refreshBtn").addEventListener("click", () => fetchState().then(() => showToast("已刷新。")).catch((err) => showToast(err.message, true)));
$("#versionRefreshBtn").addEventListener("click", () => fetchVersionInfo().then(() => showToast("版本信息已刷新。")).catch((err) => showToast(err.message, true)));
$("#systemProbeRefreshBtn").addEventListener("click", () => fetchSystemProbe().then(() => showToast("系统探针已刷新。")).catch((err) => showToast(err.message, true)));
$("#activitiesRefreshBtn").addEventListener("click", () => fetchActivities().then(() => showToast("状态已刷新。")).catch((err) => showToast(err.message, true)));
$("#memoryRefreshBtn").addEventListener("click", () => fetchMemoryFiles().then(() => showToast("记忆列表已刷新。")).catch((err) => showToast(err.message, true)));
$("#memorySummarizeBtn").addEventListener("click", () => {
  const path = state.selectedMemoryPath;
  if (path) summarizeMemoryFile(path).catch((err) => showToast(err.message, true));
});
$("#configRefreshBtn").addEventListener("click", () => fetchConfigFiles().then(() => showToast("配置列表已刷新。")).catch((err) => showToast(err.message, true)));
$("#configSaveBtn").addEventListener("click", () => saveCurrentConfig());
$("#logRefreshBtn").addEventListener("click", () => fetchLogFiles().then(() => showToast("日志列表已刷新。")).catch((err) => showToast(err.message, true)));
$("#logReloadBtn").addEventListener("click", () => reloadSelectedLog().catch((err) => showToast(err.message, true)));
$("#accessRefreshBtn").addEventListener("click", () => fetchAccessRules().then(() => showToast("权限规则已刷新。")).catch((err) => showToast(err.message, true)));
$("#pushRefreshBtn").addEventListener("click", () => fetchPushConfig().then(() => showToast("推送配置已刷新。")).catch((err) => showToast(err.message, true)));
$("#pushAddJobBtn").addEventListener("click", addPushJob);
$("#pushRunJobBtn").addEventListener("click", runSelectedPushJob);
$("#pushDeleteJobBtn").addEventListener("click", deleteSelectedPushJob);
$("#rssRefreshBtn").addEventListener("click", () => fetchRssConfig().then(() => showToast("RSS 订阅已刷新。")).catch((err) => showToast(err.message, true)));
$("#rssAddSubscriptionBtn").addEventListener("click", addRssSubscription);
$("#rssDeleteSubscriptionBtn").addEventListener("click", deleteSelectedRssSubscription);
$("#pushJobSource").addEventListener("change", () => {
  const source = (state.pushSources || []).find((item) => item.name === $("#pushJobSource").value);
  $("#pushSourceSummary").textContent = source?.description || "当前消息源未提供说明";
});
$("#aiagentPersonaSelect").addEventListener("change", (event) => {
  if (event.currentTarget.value) {
    $("#aiagentPersonaPath").value = event.currentTarget.value;
  }
});
$("#aiagentPluginToolsEnabled").addEventListener("change", () => renderAiAgentTools(true));
$("#aiagentAllowSideEffectTools").addEventListener("change", () => renderAiAgentTools(true));
$("#aiagentToolSelectionMode").addEventListener("change", () => renderAiAgentTools(true));
$("#file").addEventListener("change", updateFileHint);
$("#voiceFile").addEventListener("change", updateVoiceFileHint);

initNavigation();
initSidebar();
fetchState().catch((err) => showToast(err.message, true));
