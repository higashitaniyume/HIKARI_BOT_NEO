function renderSelects() {
  const uploadSelect = $("#existing_pack");
  const keywordSelect = $("#keywordPack");
  const inboxSelect = $("#inboxPack");
  const packMoveSelect = $("#packStickerMoveTarget");
  const voiceKeywordSelect = $("#voiceKeywordVoice");
  uploadSelect.replaceChildren(option("", "新建贴纸包"));
  keywordSelect.replaceChildren();
  inboxSelect.replaceChildren(option("", "选择已有贴纸包"));
  packMoveSelect.replaceChildren(option("", "选择目标贴纸包"));
  voiceKeywordSelect.replaceChildren();

  for (const pack of state.packs) {
    uploadSelect.append(option(pack.name, packLabel(pack)));
    keywordSelect.append(option(pack.name, packLabel(pack)));
    inboxSelect.append(option(pack.name, packLabel(pack)));
    if (pack.name !== state.selectedPackName) {
      packMoveSelect.append(option(pack.name, packLabel(pack)));
    }
  }

  for (const voice of state.voices) {
    voiceKeywordSelect.append(option(voice.id, `${voice.name} (${voice.keywords?.length || 0} 个关键词)`));
  }

  if (!state.packs.length) {
    keywordSelect.append(option("", "暂无贴纸包"));
  }
  if (!state.voices.length) {
    voiceKeywordSelect.append(option("", "暂无语音"));
  }
}

function chip(keyword, packName) {
  const node = document.createElement("span");
  node.className = "chip";
  const label = document.createElement("span");
  label.textContent = String(keyword ?? "");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "×";
  remove.title = `删除 ${packName} 的关键词 ${keyword}`;
  remove.addEventListener("click", () => deleteKeyword(packName, keyword));
  node.append(label, remove);
  return node;
}

function voiceChip(keyword, voiceId, voiceName) {
  const node = document.createElement("span");
  node.className = "chip";
  const label = document.createElement("span");
  label.textContent = String(keyword ?? "");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "×";
  remove.title = `删除 ${voiceName} 的关键词 ${keyword}`;
  remove.addEventListener("click", () => deleteVoiceKeyword(voiceId, keyword));
  node.append(label, remove);
  return node;
}

function previewUrl(stickerId) {
  return `/api/stickers/${encodeURIComponent(stickerId)}`;
}

function voiceFileUrl(voiceId) {
  return `/api/voices/${encodeURIComponent(voiceId)}/file`;
}

function renderPreviewStrip(pack) {
  const strip = document.createElement("div");
  strip.className = "preview-strip";
  const previews = Array.isArray(pack.previews) ? pack.previews : [];

  if (!previews.length) {
    strip.classList.add("empty-preview");
    strip.textContent = "暂无预览";
    return strip;
  }

  for (const stickerId of previews) {
    const frame = document.createElement("div");
    frame.className = "preview-frame";
    const image = document.createElement("img");
    image.src = previewUrl(stickerId);
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    frame.append(image);
    strip.append(frame);
  }

  return strip;
}

function renderPacks() {
  const list = $("#packList");
  list.className = "pack-list";
  list.replaceChildren();

  if (!state.packs.length) {
    list.className = "pack-list empty";
    list.textContent = "暂无贴纸包";
    return;
  }

  for (const pack of state.packs) {
    const item = document.createElement("article");
    item.className = "pack-card";
    item.classList.toggle("is-active-pack", pack.name === state.selectedPackName);

    const head = document.createElement("div");
    head.className = "pack-head";
    const title = document.createElement("div");
    title.className = "pack-title";
    title.textContent = pack.name;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${pack.count} 张`;
    const actions = document.createElement("div");
    actions.className = "pack-actions";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "icon-danger-button";
    deleteButton.title = `删除贴纸包 ${pack.name}`;
    deleteButton.setAttribute("aria-label", `删除贴纸包 ${pack.name}`);
    deleteButton.textContent = "×";
    deleteButton.addEventListener("click", () => deletePack(pack));
    const manageButton = document.createElement("button");
    manageButton.type = "button";
    manageButton.className = "small-button";
    manageButton.textContent = "管理";
    manageButton.addEventListener("click", () => fetchPackDetail(pack.name).catch((err) => showToast(err.message, true)));
    actions.append(badge, manageButton, deleteButton);
    head.append(title, actions);

    const chips = document.createElement("div");
    chips.className = "chips";
    if (pack.keywords?.length) {
      for (const keyword of pack.keywords) {
        chips.append(chip(keyword, pack.name));
      }
    } else {
      const empty = document.createElement("span");
      empty.className = "badge";
      empty.textContent = "暂无关键词";
      chips.append(empty);
    }

    item.append(renderPreviewStrip(pack), head, chips);
    list.append(item);
  }
}

function packDownloadUrl(packName) {
  return `/api/packs/${encodeURIComponent(packName)}/download`;
}

function packArchiveFileName(packName) {
  return `${String(packName || "stickers").trim() || "stickers"}.7z`;
}

function stickerDownloadFileName(sticker) {
  const rawName = String(sticker?.original_name || sticker?.file || sticker?.id || "sticker.gif").trim() || "sticker.gif";
  return rawName.toLowerCase().endsWith(".gif") ? rawName : `${rawName}.gif`;
}

function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

function filenameFromDisposition(disposition, fallback) {
  const value = String(disposition || "");
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return fallback;
    }
  }
  const asciiMatch = value.match(/filename="?([^";]+)"?/i);
  return asciiMatch ? asciiMatch[1] : fallback;
}

function clearSelectedPackDetail() {
  state.selectedPackName = "";
  state.selectedPackDetail = null;
  $("#packStickerMoveNewPack").value = "";
  render();
}

async function downloadSelectedPackArchive() {
  const packName = state.selectedPackDetail?.name || state.selectedPackName;
  if (!packName) {
    showToast("请先选择一个贴纸包。", true);
    return;
  }

  const button = $("#packDownloadBtn");
  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = "正在打包...";
  showToast("正在打包贴纸包，请稍等。");

  try {
    const res = await fetch(packDownloadUrl(packName), { cache: "no-store" });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("请先登录。");
    }
    if (!res.ok) {
      let message = "下载贴纸包失败";
      try {
        const data = await res.json();
        message = data.error || message;
      } catch {
        message = await res.text() || message;
      }
      throw new Error(message);
    }

    button.textContent = "正在下载...";
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"), packArchiveFileName(packName));
    downloadBlob(blob, filename);
    showToast("贴纸包下载已开始。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previousText || "下载 7z";
  }
}

async function downloadSticker(sticker, button) {
  if (!sticker?.id) {
    showToast("贴纸不存在。", true);
    return;
  }

  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = "下载中...";
  try {
    const res = await fetch(previewUrl(sticker.id), { cache: "no-store" });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("请先登录。");
    }
    if (!res.ok) {
      let message = "下载贴纸失败";
      try {
        const data = await res.json();
        message = data.error || message;
      } catch {
        message = await res.text() || message;
      }
      throw new Error(message);
    }

    const blob = await res.blob();
    downloadBlob(blob, stickerDownloadFileName(sticker));
    showToast("贴纸下载已开始。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previousText || "下载";
  }
}

function getSelectedPackStickerIds() {
  return Array.from(document.querySelectorAll(".pack-sticker-check:checked")).map((input) => input.value);
}

function updatePackStickerSelectionText() {
  const selected = getSelectedPackStickerIds();
  const selectedSet = new Set(selected);
  for (const card of document.querySelectorAll(".sticker-card")) {
    card.classList.toggle("is-selected", selectedSet.has(card.dataset.stickerId));
  }
  $("#packStickerSelectedText").textContent = `已选择 ${selected.length} 张`;
  const stickers = state.selectedPackDetail?.stickers || [];
  const allBox = $("#packStickerSelectAll");
  allBox.checked = Boolean(stickers.length) && selected.length === stickers.length;
  allBox.indeterminate = selected.length > 0 && selected.length < stickers.length;
}

function renderPackDetail() {
  const detail = state.selectedPackDetail;
  const list = $("#packStickerList");
  const downloadButton = $("#packDownloadBtn");
  const clearButton = $("#packClearBtn");
  list.replaceChildren();

  if (!detail) {
    $("#packDetailTitle").textContent = "贴纸包内容";
    $("#packDetailMeta").textContent = "选择上方贴纸包后查看和管理具体贴纸。";
    downloadButton.hidden = true;
    clearButton.hidden = true;
    list.className = "sticker-grid empty";
    list.textContent = "暂无选中的贴纸包";
    updatePackStickerSelectionText();
    return;
  }

  $("#packDetailTitle").textContent = detail.name;
  $("#packDetailMeta").textContent = `${detail.count || 0} 张贴纸 / ${detail.keywords?.length || 0} 个关键词`;
  downloadButton.hidden = false;
  downloadButton.disabled = false;
  downloadButton.textContent = "下载 7z";
  clearButton.hidden = false;

  const stickers = detail.stickers || [];
  if (!stickers.length) {
    list.className = "sticker-grid empty";
    list.textContent = "这个贴纸包里暂无贴纸";
    updatePackStickerSelectionText();
    return;
  }

  list.className = "sticker-grid";
  for (const sticker of stickers) {
    const card = document.createElement("article");
    card.className = "sticker-card";
    card.dataset.stickerId = sticker.id;

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "pack-sticker-check";
    check.value = sticker.id;
    check.setAttribute("aria-label", `选择贴纸 ${sticker.original_name || sticker.id}`);
    check.addEventListener("change", updatePackStickerSelectionText);

    const frame = document.createElement("a");
    frame.className = "sticker-frame";
    frame.href = previewUrl(sticker.id);
    frame.target = "_blank";
    frame.rel = "noopener";
    const image = document.createElement("img");
    image.src = previewUrl(sticker.id);
    image.alt = sticker.original_name || sticker.id;
    image.loading = "lazy";
    image.decoding = "async";
    frame.append(image);

    const title = document.createElement("div");
    title.className = "sticker-title";
    title.textContent = sticker.original_name || sticker.file || sticker.id;

    const meta = document.createElement("div");
    meta.className = "sticker-meta";
    meta.textContent = `${formatBytes(sticker.size)} / ${formatTime(sticker.created_at)}${sticker.missing ? " / 文件缺失" : ""}`;

    const stickerDownloadButton = document.createElement("button");
    stickerDownloadButton.type = "button";
    stickerDownloadButton.className = "small-button sticker-download-button";
    stickerDownloadButton.textContent = "下载";
    stickerDownloadButton.disabled = Boolean(sticker.missing);
    stickerDownloadButton.addEventListener("click", () => downloadSticker(sticker, stickerDownloadButton));

    card.append(check, frame, title, meta, stickerDownloadButton);
    list.append(card);
  }
  updatePackStickerSelectionText();
}

function renderKeywords() {
  const list = $("#keywordList");
  list.className = "list";
  list.replaceChildren();

  if (!state.keywords.length) {
    list.className = "list empty";
    list.textContent = "暂无关键词";
    return;
  }

  for (const relation of state.keywords) {
    const item = document.createElement("article");
    item.className = "keyword-card";

    const head = document.createElement("div");
    head.className = "item-head";
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = relation.keyword;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${relation.packs.length} 个包`;
    head.append(title, badge);

    const chips = document.createElement("div");
    chips.className = "chips";
    for (const packName of relation.packs) {
      const packChip = document.createElement("span");
      packChip.className = "chip";
      packChip.textContent = packName;
      chips.append(packChip);
    }

    item.append(head, chips);
    list.append(item);
  }
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
