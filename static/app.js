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
    // 只把带面板且没标 hidden 的模块放侧栏：没有 [ui] 的（如 gacha）只提供接口，
    // hidden 的（如数据同步）面板内嵌在概览页
    ...state.modules
      .filter((module) => module.ui?.panel && !module.ui?.hidden)
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

function renderOnboarding(info) {
  const setup = info.setup;
  if (!setup || !setup.first_run) return "";
  const dataDir = info.paths?.data || "";
  const mdbPath = info.paths?.mdb || "";
  const importHint = setup.can_import_via_ui
    ? `用模拟器或已连接的手机：在下面「数据版本」卡里点「检查客户端」，再导入这一版；手上只有一个文件：在同一处直接选 <span class="mono">master.mdb</span> 上传。`
    : `「数据同步」模块当前不可用，请把 <span class="mono">master.mdb</span> 放到 <span class="mono">${escapeHtml(mdbPath)}</span> 后重启。`;
  const cliHint = info.app.frozen
    ? ""
    : `<p class="hint">命令行方式：<span class="mono">uv run python scripts/import_master_mdb.py &lt;master.mdb&gt; --game-version &lt;版本&gt;</span></p>`;
  const degraded = (setup.degraded_modules || [])
    .map(
      (m) =>
        `<li><span class="mono">${escapeHtml(m.id)}</span>：${escapeHtml(m.error || "未加载")}</li>`,
    )
    .join("");
  return `
    <section class="card onboarding">
      <h2>从这里开始</h2>
      <p class="hint">当前还没有结构化数据。下面三步把应用跑起来，第 2 步可以跳过。</p>
      <ol class="steps">
        <li>
          <div class="step-title">导入游戏数据 <span class="pill muted">必做</span></div>
          <p class="hint">${importHint}</p>
          <p class="hint">导入后的数据库、日志与快照都在 <span class="mono">${escapeHtml(dataDir)}</span>。</p>
          ${cliHint}
        </li>
        <li>
          <div class="step-title">配置知识库问答 <span class="pill muted">可选</span></div>
          <p class="hint">在 <span class="mono">.env</span> 里填 <span class="mono">DEEPSEEK_API_KEY</span> 后重启，才能调用模型问答；不填也能用图鉴、日程、卡池、赛道、技能、相性。</p>
        </li>
        <li>
          <div class="step-title">开始查询 <span class="pill ok">导入后可用</span></div>
          <p class="hint">从左侧进入各模块；每处结论都会标注当前数据版本，重新导入后版本号随之变化。</p>
        </li>
      </ol>
      ${degraded ? `<h3>当前不可用的模块</h3><ul class="steps plain">${degraded}</ul>` : ""}
    </section>`;
}

function versionRows(data) {
  const datasets = Object.entries(data.datasets || {});
  // 版本号要一直看得见；逐表条数属于内部细节，折起来
  const datasetBlock = datasets.length
    ? `<details style="margin-top:10px">
        <summary>数据集条数（${datasets.length} 项）</summary>
        <dl class="kv" style="margin-top:8px">
          ${datasets
            .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`)
            .join("")}
        </dl>
      </details>`
    : "";
  return `
    <dl class="kv">
      <dt>版本</dt><dd>${escapeHtml(data.version)}</dd>
      <dt>游戏版本</dt><dd>${escapeHtml(data.game_version || "未记录")}</dd>
      <dt>更新于</dt><dd>${escapeHtml(data.updated_at || "未记录")}</dd>
      <dt>来源</dt><dd>${escapeHtml(data.source || "未记录")}</dd>
    </dl>
    ${datasetBlock}`;
}

// 导入数据后只刷新这张卡的内容，避免整页重绘把面板状态（检查结果、历史展开）冲掉
function patchOverviewVersion() {
  const container = document.getElementById("overview-version");
  if (container && state.info?.data) container.innerHTML = versionRows(state.info.data);
}

async function renderOverview() {
  const info = state.info;
  const data = info.data;
  view.innerHTML = `
    <h1 class="page-title">概览</h1>
    <p class="page-desc">所有事实性结论都应能追溯到下面的数据版本；导入或更新数据后，版本号会随之变化。</p>
    ${renderOnboarding(info)}
    <div class="grid two">
      <section class="card">
        <h2>数据版本</h2>
        <div id="overview-version">${versionRows(data)}</div>
        ${
          data.known
            ? ""
            : `<p class="hint">还没有结构化数据，用下面的入口导入一次即可。</p>`
        }
        <div id="overview-sync" style="margin-top:18px"></div>
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

  // 数据同步的面板内嵌在这张「数据版本」卡里（manifest 里标了 hidden，不进侧栏）
  const syncHost = document.getElementById("overview-sync");
  const meta = state.modules.find((m) => m.id === "sync");
  if (syncHost && meta?.status === "ok" && meta.ui?.panel) {
    try {
      const panel = await loadPanel(meta);
      const result = await panel.mount(syncHost, panelContext({ embedded: true }));
      if (typeof result === "function") state.cleanups.push(result);
    } catch (error) {
      syncHost.innerHTML = `<p class="hint">数据同步面板加载失败：${escapeHtml(
        error.message,
      )}</p>`;
    }
  }
}

async function refreshInfo() {
  state.info = await api("/api/system/info");
  state.modules = state.info.modules;
  document.getElementById("app-version").textContent = `v${state.info.app.version}`;
  renderVersionBadge();
  // 在概览页导入完数据后，这张卡的版本号与条数要立刻跟着变
  patchOverviewVersion();
  return state.info;
}

async function loadPanel(meta) {
  if (!state.panels.has(meta.id)) {
    // 带版本参数，应用升级后不会复用浏览器缓存的旧面板
    const version = `${state.info?.app?.version ?? "dev"}-${meta.version}`;
    const imported = await import(`${meta.ui.panel}?v=${encodeURIComponent(version)}`);
    state.panels.set(meta.id, imported.default);
  }
  return state.panels.get(meta.id);
}

function panelContext(extra = {}) {
  return {
    api,
    escapeHtml,
    toast,
    createLimiter,
    state,
    // hash 里的查询参数，例如 #/skills?skill=200462 的 {skill: "200462"}
    params: state.params || new URLSearchParams(),
    // 模块改了数据后（例如导入了新版本）用它刷新侧栏徽标与模块列表
    refreshInfo,
    ...extra,
  };
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

  const panel = await loadPanel(meta);
  view.innerHTML = "";
  const root = document.createElement("div");
  view.appendChild(root);
  const result = await panel.mount(root, panelContext());
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
      await renderOverview();
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
