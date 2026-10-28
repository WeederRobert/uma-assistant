const state = {
  info: null,
  modules: [],
  panels: new Map(),
  activeId: null,
  cleanups: [],
};

const view = document.getElementById("view");
const nav = document.getElementById("nav");
const toastEl = document.getElementById("toast");

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || (detail ? JSON.stringify(detail) : `HTTP ${response.status}`);
    throw new Error(message);
  }
  return data;
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

export function toast(message, kind = "info") {
  toastEl.textContent = message;
  toastEl.className = `toast ${kind === "error" ? "error" : ""}`;
  toastEl.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    toastEl.hidden = true;
  }, 4200);
}

/**
 * 长列表的「先显示 N 条，可展开」控制器。
 *
 * 每个面板在自己的闭包里建一个实例，key 区分不同区块（如 `gacha:upcoming`）。
 * 只负责切片与按钮文案，视图仍由面板自己的渲染函数负责。
 */
export function createLimiter(defaultLimit = 8) {
  const expanded = new Set();
  return {
    isOpen: (key) => expanded.has(key),
    toggle(key) {
      if (expanded.has(key)) expanded.delete(key);
      else expanded.add(key);
      return expanded.has(key);
    },
    slice(key, items) {
      return expanded.has(key) ? items : items.slice(0, defaultLimit);
    },
    more(key, total) {
      const hidden = total - defaultLimit;
      if (hidden <= 0) return "";
      return `<button type="button" class="more-btn" data-more="${escapeHtml(key)}">${
        expanded.has(key) ? "收起" : `展开其余 ${hidden} 条`
      }</button>`;
    },
  };
}

function renderVersionBadge() {
  const badge = document.getElementById("version-badge");
  const data = state.info?.data;
  if (!data) return;
  badge.classList.toggle("known", Boolean(data.known));
  badge.classList.toggle("unknown", !data.known);
  badge.textContent = data.known
    ? `数据 ${data.version}${data.game_version ? ` · 游戏 ${data.game_version}` : ""}`
    : "数据版本未知 · 未导入 master.mdb";
  badge.title = data.known
    ? [`更新于 ${data.updated_at || "未知"}`, `来源 ${data.source || "未知"}`, JSON.stringify(data.datasets || {})].join("\n")
    : "运行 scripts/import_master_mdb.py 导入国服 master.mdb 后显示版本";
}

function renderSidebar() {
  const items = [
    { id: "overview", label: "概览", status: "ok" },
    // 只把带面板的模块放侧栏：没有 [ui] 的模块（如只提供接口的 gacha）在概览里列状态即可
    ...state.modules
      .filter((module) => module.ui?.panel)
      .map((m) => ({
        id: m.id,
        label: m.ui?.label || m.name,
        status: m.status,
      })),
  ];
  nav.innerHTML = items
    .map(
      (item) => `
      <button class="nav-item ${item.id === state.activeId ? "active" : ""} ${
        item.status === "error" ? "error" : ""
      }" data-id="${escapeHtml(item.id)}">
        <span class="dot"></span>${escapeHtml(item.label)}
      </button>`,
    )
    .join("");
  nav.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      location.hash = `#/${button.dataset.id}`;
    });
  });
}

function cleanup() {
  for (const fn of state.cleanups.splice(0)) {
    try {
      fn();
    } catch (error) {
      console.warn("cleanup failed", error);
    }
  }
}

function renderOverview() {
  const info = state.info;
  const data = info.data;
  const datasets = Object.entries(data.datasets || {})
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`)
    .join("");
  view.innerHTML = `
    <h1 class="page-title">概览</h1>
    <p class="page-desc">所有事实性结论都应能追溯到下面的数据版本；导入或更新数据后，版本号会随之变化。</p>
    <div class="grid two">
      <section class="card">
        <h2>数据版本</h2>
        <dl class="kv">
          <dt>版本</dt><dd>${escapeHtml(data.version)}</dd>
          <dt>游戏版本</dt><dd>${escapeHtml(data.game_version || "未记录")}</dd>
          <dt>更新于</dt><dd>${escapeHtml(data.updated_at || "未记录")}</dd>
          <dt>来源</dt><dd>${escapeHtml(data.source || "未记录")}</dd>
          ${datasets}
        </dl>
        ${
          data.known
            ? ""
            : `<p class="hint">还没有结构化数据。把国服 <span class="mono">master.mdb</span> 放到本机后执行：
              <br /><span class="mono">uv run python scripts/import_master_mdb.py &lt;path&gt; --game-version &lt;版本&gt;</span></p>`
        }
      </section>
      <section class="card">
        <h2>运行环境</h2>
        <dl class="kv">
          <dt>应用版本</dt><dd>${escapeHtml(info.app.version)}</dd>
          <dt>Python</dt><dd>${escapeHtml(info.app.python)}</dd>
          <dt>平台</dt><dd>${escapeHtml(info.app.platform)}</dd>
        </dl>
        <h3 style="margin-top:18px">模块</h3>
        <div class="module-list">
          ${info.modules
            .map(
              (m) => `
            <div class="module-row">
              <div>
                <div>${escapeHtml(m.name)} <span class="mono">${escapeHtml(m.version)}</span></div>
                <div class="meta">${escapeHtml(m.description || "")}</div>
                ${m.error ? `<div class="meta" style="color:var(--danger)">${escapeHtml(m.error)}</div>` : ""}
              </div>
              <span class="pill ${m.status === "ok" ? "ok" : "error"}">${m.status === "ok" ? "正常" : "异常"}</span>
            </div>`,
            )
            .join("")}
        </div>
      </section>
    </div>
  `;
}

async function refreshInfo() {
  state.info = await api("/api/system/info");
  state.modules = state.info.modules;
  document.getElementById("app-version").textContent = `v${state.info.app.version}`;
  renderVersionBadge();
  return state.info;
}

async function renderModule(moduleId) {
  const meta = state.modules.find((m) => m.id === moduleId);
  if (!meta) {
    view.innerHTML = `<div class="empty">模块不存在：${escapeHtml(moduleId)}</div>`;
    return;
  }
  if (meta.status !== "ok" || !meta.ui?.panel) {
    view.innerHTML = `
      <h1 class="page-title">${escapeHtml(meta.name)}</h1>
      <div class="empty">
        ${meta.error ? escapeHtml(meta.error) : "该模块没有提供界面，可直接调用接口。"}
      </div>`;
    return;
  }

  if (!state.panels.has(meta.id)) {
    // 带版本参数，应用升级后不会复用浏览器缓存的旧面板
    const version = `${state.info?.app?.version ?? "dev"}-${meta.version}`;
    const imported = await import(`${meta.ui.panel}?v=${encodeURIComponent(version)}`);
    state.panels.set(meta.id, imported.default);
  }
  const panel = state.panels.get(meta.id);
  view.innerHTML = "";
  const root = document.createElement("div");
  view.appendChild(root);
  const result = await panel.mount(root, {
    api,
    escapeHtml,
    toast,
    createLimiter,
    state,
    // hash 里的查询参数，例如 #/skills?skill=200462 的 {skill: "200462"}
    params: state.params || new URLSearchParams(),
    // 模块改了数据后（例如导入了新版本）用它刷新侧栏徽标与模块列表
    refreshInfo,
  });
  if (typeof result === "function") {
    state.cleanups.push(result);
  }
}

async function route() {
  cleanup();
  const raw = location.hash.replace(/^#\/?/, "") || "overview";
  // 支持 #/skills?skill=200462 这种带参数的入口（面板用 ctx.params 读）
  const [requested, query = ""] = raw.split("?");
  state.activeId = requested;
  state.params = new URLSearchParams(query);
  renderSidebar();
  try {
    if (requested === "overview") {
      renderOverview();
    } else {
      await renderModule(requested);
    }
  } catch (error) {
    console.error(error);
    view.innerHTML = `<div class="empty">页面加载失败：${escapeHtml(error.message)}</div>`;
  }
}

async function boot() {
  try {
    await refreshInfo();
  } catch (error) {
    view.innerHTML = `<div class="empty">无法连接本地服务：${escapeHtml(error.message)}</div>`;
    return;
  }
  window.addEventListener("hashchange", route);
  if (!location.hash) {
    location.hash = "#/overview";
  } else {
    await route();
  }
}

boot();
