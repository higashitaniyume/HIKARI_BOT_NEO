const state = {
  packs: [],
  keywords: [],
};

const $ = (selector) => document.querySelector(selector);

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  toast.classList.toggle("error", isError);
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

async function readJsonResponse(res, fallbackMessage) {
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("请先登录。");
  }

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || fallbackMessage);
  }
  return data;
}

function option(value, text) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = text;
  return node;
}

async function fetchState() {
  const res = await fetch("/api/state", { cache: "no-store" });
  const data = await readJsonResponse(res, "读取贴纸数据失败");
  state.packs = data.packs || [];
  state.keywords = data.keywords || [];
  render();
}

function renderSelects() {
  const uploadSelect = $("#existing_pack");
  const keywordSelect = $("#keywordPack");
  uploadSelect.replaceChildren(option("", "新建贴纸包"));
  keywordSelect.replaceChildren();

  for (const pack of state.packs) {
    uploadSelect.append(option(pack.name, `${pack.name} (${pack.count} 个)`));
    keywordSelect.append(option(pack.name, `${pack.name} (${pack.count} 个)`));
  }

  if (!state.packs.length) {
    keywordSelect.append(option("", "暂无贴纸包"));
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

function renderPacks() {
  const list = $("#packList");
  list.className = "list";
  list.replaceChildren();

  if (!state.packs.length) {
    list.className = "list empty";
    list.textContent = "暂无贴纸包";
    return;
  }

  for (const pack of state.packs) {
    const item = document.createElement("article");
    item.className = "item";

    const head = document.createElement("div");
    head.className = "item-head";
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = pack.name;
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${pack.count} 张`;
    head.append(title, badge);

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

    item.append(head, chips);
    list.append(item);
  }
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
    item.className = "item";

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

function render() {
  $("#packCount").textContent = state.packs.length;
  $("#keywordCount").textContent = state.keywords.length;
  renderSelects();
  renderPacks();
  renderKeywords();
}

async function addKeyword(event) {
  event.preventDefault();
  const pack = $("#keywordPack").value;
  const keyword = $("#keywordInput").value.trim();
  if (!pack || !keyword) {
    showToast("请选择贴纸包并填写关键词。", true);
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
    $("#keywordInput").value = "";
    render();
    showToast("关键词关联已添加。");
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
    render();
    showToast("关键词关联已删除。");
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#keywordForm").addEventListener("submit", addKeyword);
$("#refreshBtn").addEventListener("click", () => fetchState().then(() => showToast("已刷新。")).catch((err) => showToast(err.message, true)));

fetchState().catch((err) => showToast(err.message, true));
