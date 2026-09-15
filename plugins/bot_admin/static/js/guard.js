// 风控页：group_guard + self_review 两份配置的读写。
// 表单字段用 data-guard-field="a.b.c" 声明配置路径，渲染和收集都走同一份声明。

const GUARD_FORMS = {
  group_guard: { form: "#guardGroupForm", meta: "#guardGroupMeta", label: "群风控" },
  self_review: { form: "#guardSelfForm", meta: "#guardSelfMeta", label: "出站自审查" },
};

function guardFields(plugin) {
  const form = $(GUARD_FORMS[plugin].form);
  return Array.from(form.querySelectorAll("[data-guard-field]"));
}

function guardReadPath(source, path) {
  let cursor = source;
  for (const key of path.split(".")) {
    if (cursor === null || typeof cursor !== "object") return undefined;
    cursor = cursor[key];
  }
  return cursor;
}

function guardWritePath(target, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  let cursor = target;
  for (const key of keys) {
    if (typeof cursor[key] !== "object" || cursor[key] === null) {
      cursor[key] = {};
    }
    cursor = cursor[key];
  }
  cursor[last] = value;
}

function renderGuardConfig() {
  const plugins = state.guardConfig.plugins || {};
  $("#guardReviewModel").textContent = state.guardConfig.review_model || "未配置模型";

  for (const [plugin, refs] of Object.entries(GUARD_FORMS)) {
    const entry = plugins[plugin];
    const meta = $(refs.meta);
    if (!entry) {
      meta.textContent = `读取 ${plugin} 配置失败。`;
      continue;
    }
    const file = entry.file;
    meta.textContent = file
      ? `BotData/plugin_configs/${plugin}.json / ${formatTime(file.mtime || 0)}`
      : `BotData/plugin_configs/${plugin}.json（尚未生成，保存后创建）`;

    const config = entry.config || {};
    for (const el of guardFields(plugin)) {
      const value = guardReadPath(config, el.dataset.guardField);
      if (el.type === "checkbox") {
        el.checked = value === true;
      } else if (el.dataset.guardList !== undefined) {
        el.value = joinIds(value);
      } else {
        el.value = value ?? "";
      }
    }
  }
}

function buildGuardPayload(plugin) {
  const config = {};
  for (const el of guardFields(plugin)) {
    const path = el.dataset.guardField;
    if (el.type === "checkbox") {
      guardWritePath(config, path, el.checked);
    } else if (el.dataset.guardList !== undefined) {
      guardWritePath(config, path, splitIds(el.value));
    } else if (el.type === "number") {
      // 留空时不提交，让后端保留当前值而不是回落到出厂默认。
      if (el.value.trim()) guardWritePath(config, path, Number(el.value));
    } else {
      guardWritePath(config, path, el.value);
    }
  }
  return { plugin, config };
}

async function saveGuardConfig(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const plugin = form.dataset.guardPlugin;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const res = await fetch("/api/guard-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildGuardPayload(plugin)),
    });
    const data = await readJsonResponse(res, `保存${GUARD_FORMS[plugin].label}设置失败`);
    state.guardConfig = data;
    renderGuardConfig();
    showToast(data.message || "风控设置已保存。");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function resetGuardPrompt(plugin) {
  const entry = (state.guardConfig.plugins || {})[plugin];
  if (!entry) {
    showToast("配置还没读取完成，请先刷新。", true);
    return;
  }
  const field = $(GUARD_FORMS[plugin].form).querySelector('[data-guard-field="review.prompt"]');
  field.value = entry.default_prompt || "";
  showToast("已填回默认提示词，记得保存。");
}
