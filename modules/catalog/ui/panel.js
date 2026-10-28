const API = "/api/catalog";

const KIND_LABEL = {
  character: "马娘",
  support_card: "协助卡",
  skill: "技能",
};

// 导入时间以 UTC 入库，按系统时区换算后再显示
function shortTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
  const pad2 = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours(),
  )}:${pad2(date.getMinutes())}`;
}

function renderAttributes(escapeHtml, attributes) {
  return (attributes || [])
    .map(
      (row) =>
        `<span class="attr"><span class="attr-key">${escapeHtml(row.label)}</span>${escapeHtml(
          row.value,
        )}</span>`,
    )
    .join("");
}

const NEED_RANK_LABEL = (rank) => (rank ? `★${rank} 解锁` : "初始");

function skillList(escapeHtml, rows) {
  if (!rows || !rows.length) return `<div class="empty">没有可查的技能</div>`;
  return `<div class="list">${rows
    .map(
      (row) => `
      <div class="list-row clickable" role="button" tabindex="0" data-skill-link="${escapeHtml(
        row.skill_id,
      )}" aria-label="在「技能」页查看 ${escapeHtml(row.name)}">
        <div>
          <div class="name">
            ${row.rarity_label ? `<span class="pill">${escapeHtml(row.rarity_label)}</span> ` : ""}
            ${escapeHtml(row.name)}
          </div>
          <div class="meta clamp">${escapeHtml(row.description || "无描述")}</div>
        </div>
        <span class="mono nowrap">${
          row.need_rank === undefined
            ? `#${escapeHtml(row.skill_id)}`
            : escapeHtml(NEED_RANK_LABEL(row.need_rank))
        }</span>
      </div>`,
    )
    .join("")}</div>`;
}

function effectTable(escapeHtml, rows) {
  if (!rows || !rows.length) return `<div class="empty">这张卡没有训练效果数据</div>`;
  const body = rows
    .map((row) => {
      const levels = Object.entries(row.levels || {})
        .map(([level, value]) => `${level}: +${value}`)
        .join(" · ");
      const plus = (value) =>
        value === null || value === undefined ? "—" : `+${value}`;
      return `<tr>
        <td>${escapeHtml(row.label)}</td>
        <td class="mono">${row.effect_type}</td>
        <td class="mono">${plus(row.init)}</td>
        <td class="mono">${escapeHtml(levels || "—")}</td>
        <td class="mono">${plus(row.max)}</td>
        <td class="mono">${plus(row.gain)}</td>
      </tr>`;
    })
    .join("");
  return `<table class="seg-table">
    <thead><tr><th>效果</th><th>类型</th><th>初始</th><th>各突破等级</th><th>满破</th><th>净增</th></tr></thead>
    <tbody>${body}</tbody>
  </table>`;
}

function uniqueList(escapeHtml, rows) {
  if (!rows || !rows.length) return "";
  return `<ul class="hint" style="margin:0">${rows
    .map(
      (row) =>
        `<li>${escapeHtml(row.label)} ${escapeHtml(String(row.value ?? "—"))}${
          row.need_lv ? `（${row.need_lv} 级解锁）` : ""
        }<span class="mono"> · 类型 ${escapeHtml(String(row.effect_type))}</span></li>`,
    )
    .join("")}</ul>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, createLimiter } = ctx;
    const limiter = createLimiter(9);
    let lastItems = [];
    let stats = null;

    root.innerHTML = `
      <h1 class="page-title">图鉴查询</h1>
      <p class="page-desc">数据来自国服 master.mdb 的本地快照。点击任意一条查看完整描述、天赋加成与数据版本。</p>
      <div class="grid">
        <section class="card">
          <div class="toolbar" style="margin-bottom:0">
            <input type="search" id="cat-q" placeholder="输入马娘、协助卡或技能名" style="min-width:280px" />
            <select id="cat-kind">
              <option value="">全部</option>
              <option value="character">马娘</option>
              <option value="support_card">协助卡</option>
              <option value="skill">技能</option>
            </select>
            <button class="primary" id="cat-search">搜索</button>
            <span class="hint" id="cat-stats"></span>
          </div>
        </section>
        <section id="cat-results" class="list grid"></section>
      </div>
      <dialog id="cat-dialog" class="modal" aria-labelledby="cat-dialog-title">
        <div class="modal-head">
          <div>
            <div class="pill" id="cat-dialog-kind"></div>
            <h2 id="cat-dialog-title"></h2>
          </div>
          <button class="ghost" id="cat-dialog-close" type="button" aria-label="关闭详情（Esc）">关闭</button>
        </div>
        <div class="modal-body" id="cat-dialog-body"></div>
        <div class="modal-foot hint" id="cat-dialog-foot"></div>
      </dialog>
    `;

    const resultsEl = root.querySelector("#cat-results");
    const statsEl = root.querySelector("#cat-stats");
    const queryEl = root.querySelector("#cat-q");
    const kindEl = root.querySelector("#cat-kind");
    const dialog = root.querySelector("#cat-dialog");
    const dialogTitle = root.querySelector("#cat-dialog-title");
    const dialogKind = root.querySelector("#cat-dialog-kind");
    const dialogBody = root.querySelector("#cat-dialog-body");
    const dialogFoot = root.querySelector("#cat-dialog-foot");

    async function loadStats() {
      stats = await api(`${API}/stats`);
      const counts = stats.counts;
      statsEl.textContent = `马娘 ${counts.characters} · 协助卡 ${counts.support_cards} · 技能 ${counts.skills} · 数据 ${stats.data_version.version}`;
      if (!stats.data_version.known || !counts.characters) {
        resultsEl.innerHTML = `
          <div class="empty">
            还没有导入国服数据。先在本机找到 <span class="mono">master.mdb</span>，然后运行：<br /><br />
            <span class="mono">uv run python scripts/import_master_mdb.py "&lt;path&gt;\\master.mdb" --game-version &lt;国服版本&gt;</span>
            <br /><br />导入后刷新页面，这里会显示马娘、协助卡与技能。
          </div>`;
        return false;
      }
      return true;
    }

    function openDialog() {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }

    function closeDialog() {
      if (dialog.open && typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }

    async function showDetail(kind, id) {
      try {
        const item = await api(`${API}/items/${kind}/${id}`);
        dialogKind.textContent = KIND_LABEL[item.kind] || item.kind;
        dialogTitle.textContent = item.title ? `${item.name} · ${item.title}` : item.name;

        const description = item.description
          ? `<pre class="detail-desc">${escapeHtml(item.description)}</pre>`
          : "";
        const sections = [];
        if (item.kind === "character" && item.skills) {
          sections.push(
            `<h3 style="margin-top:16px">技能（${item.skills.length}）</h3>${skillList(
              escapeHtml,
              item.skills,
            )}`,
          );
        }
        if (item.kind === "support_card") {
          sections.push(
            `<h3 style="margin-top:16px">训练效果（${(item.effects || []).length}）</h3>`,
            effectTable(escapeHtml, item.effects),
            item.effect_source
              ? `<p class="hint">效果名称取自 ${escapeHtml(
                  item.effect_source,
                )}；主数据里没有这张字典，未收录的类型只显示编号。突破等级值为「该档达到的值」，-1 表示该档不提升；「净增」是满破值减去初始值。</p>`
              : "",
            `<h3 style="margin-top:16px">固有加成</h3>`,
            uniqueList(escapeHtml, item.unique_effects) ||
              `<p class="hint" style="margin:0">这张卡没有固有加成数据。</p>`,
            `<h3 style="margin-top:16px">提示技能（${(item.skills || []).length}）</h3>`,
            skillList(escapeHtml, item.skills),
          );
        }
        dialogBody.innerHTML = `
          <div class="attrs">${renderAttributes(escapeHtml, item.attributes)}</div>
          ${description}
          ${sections.join("")}
          <dl class="kv detail-ids">
            <dt>条目 ID</dt><dd>${escapeHtml(item.id)}</dd>
          </dl>`;

        const version = item.data_version || {};
        const updatedAt = shortTime(version.updated_at);
        dialogFoot.textContent = version.known
          ? `数据版本 ${version.version}${version.game_version ? ` · 游戏 ${version.game_version}` : ""} · 更新于 ${updatedAt || "未记录"} · 来源 ${version.source || "未记录"}`
          : "数据版本未知：当前条目来自未标记版本的导入。";
        openDialog();
      } catch (error) {
        toast(error.message, "error");
      }
    }

    function renderItems(items) {
      lastItems = items;
      if (!items.length) {
        resultsEl.innerHTML = `<div class="empty">没有匹配结果</div>`;
        return;
      }
      resultsEl.innerHTML = `${limiter
        .slice("catalog", items)
        .map(
          (item) => `
            <div class="list-row clickable" role="button" tabindex="0"
                 data-kind="${escapeHtml(item.kind)}" data-id="${escapeHtml(item.id)}"
                 aria-label="查看 ${escapeHtml(item.name)} 的详情">
              <div>
                <div class="name">
                  <span class="pill">${escapeHtml(KIND_LABEL[item.kind] || item.kind)}</span>
                  ${escapeHtml(item.name)}${item.title ? ` · ${escapeHtml(item.title)}` : ""}
                </div>
                <div class="meta clamp">${renderAttributes(escapeHtml, item.attributes)}${
                  item.description ? `<br />${escapeHtml(item.description)}` : ""
                }</div>
              </div>
              <span class="mono">#${escapeHtml(item.id)}</span>
            </div>`,
        )
        .join("")}${limiter.more("catalog", items.length)}`;
    }

    async function search() {
      const query = queryEl.value.trim();
      if (!query) return;
      const kind = kindEl.value;
      const params = new URLSearchParams({ q: query, limit: "30" });
      if (kind) params.set("kind", kind);
      try {
        const result = await api(`${API}/search?${params.toString()}`);
        renderItems(result.items);
      } catch (error) {
        toast(error.message, "error");
      }
    }

    function onResultClick(event) {
      const row = event.target.closest(".list-row.clickable");
      if (!row) return;
      showDetail(row.dataset.kind, row.dataset.id);
    }

    function onResultKeydown(event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest(".list-row.clickable");
      if (!row) return;
      event.preventDefault();
      showDetail(row.dataset.kind, row.dataset.id);
    }

    // <dialog> 自带 Esc 关闭；这里同时兜住旧浏览器与「点遮罩关闭」的路径
    function onDialogKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDialog();
      }
    }

    function onDialogClick(event) {
      if (event.target === dialog) closeDialog();
    }

    root.querySelector("#cat-search").addEventListener("click", search);
    root.querySelector("#cat-dialog-close").addEventListener("click", closeDialog);
    queryEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        search();
      }
    });
    resultsEl.addEventListener("click", onResultClick);
    resultsEl.addEventListener("keydown", onResultKeydown);
    resultsEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-more]");
      if (!button || button.dataset.more !== "catalog") return;
      limiter.toggle("catalog");
      renderItems(lastItems);
    });
    dialog.addEventListener("keydown", onDialogKeydown);
    dialog.addEventListener("click", onDialogClick);
    // 详情里的技能行可跳到「技能」页看条件与效果参数
    dialogBody.addEventListener("click", (event) => {
      const row = event.target.closest("[data-skill-link]");
      if (!row) return;
      closeDialog();
      location.hash = `#/skills?skill=${row.dataset.skillLink}`;
    });

    await loadStats();

    return () => {
      resultsEl.removeEventListener("click", onResultClick);
      resultsEl.removeEventListener("keydown", onResultKeydown);
      dialog.removeEventListener("keydown", onDialogKeydown);
      dialog.removeEventListener("click", onDialogClick);
    };
  },
};
