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

const PLUS = (value) => (value === null || value === undefined ? "—" : `+${value}`);

// 每列 = 「突破到该档并升到该档等级上限」时的数值；与上一档不同时加粗，方便扫出变化点
function breakthroughTable(escapeHtml, breakthroughs) {
  if (!breakthroughs || !(breakthroughs.level_caps || []).length) {
    return `<p class="hint" style="margin:0">缺少这张卡的等级上限数据（support_card_limit），无法按突破换算；请重新导入 master.mdb。</p>`;
  }
  const caps = breakthroughs.level_caps;
  const rows = breakthroughs.rows || [];
  if (!rows.length) return `<div class="empty">这张卡没有训练效果数据</div>`;

  const head = caps
    .map(
      (cap, index) =>
        `<th>${index}突<br /><span class="hint">上限 ${escapeHtml(cap)} 级</span></th>`,
    )
    .join("");

  const body = rows
    .map((row) => {
      const values = row.values || [];
      const cells = caps
        .map((_cap, index) => {
          const value = values[index];
          const changed = index > 0 && value !== values[index - 1];
          return `<td class="mono">${changed ? `<strong>${PLUS(value)}</strong>` : PLUS(value)}</td>`;
        })
        .join("");
      return `<tr><td>${escapeHtml(row.label)}</td>${cells}</tr>`;
    })
    .join("");

  return `<table class="seg-table">
    <thead><tr><th>效果</th>${head}</tr></thead>
    <tbody>${body}</tbody>
  </table>`;
}

// 解锁条件：主数据给的是「解锁等级」，换算成「第几突起」更贴近用户问的「什么时候用得上」
function unlockText(unique) {
  if (!unique || !unique.need_lv) return "无解锁等级，初始即可用";
  const cap = unique.unlock_breakthrough;
  return cap === null || cap === undefined
    ? `等级 ${unique.need_lv}（该稀有度练不到这个等级）`
    : `等级 ${unique.need_lv} · 第 ${cap} 突起可达到`;
}

function uniqueBlock(escapeHtml, unique) {
  if (!unique) return `<p class="hint" style="margin:0">这张卡没有固有加成数据。</p>`;
  // 条件型加成（101–120）的数值是条件参数，含义随类型变化，只用官方成句文案说明
  const parts = [];
  if (unique.text) {
    parts.push(`<div class="detail-desc" style="margin:0 0 6px">${escapeHtml(unique.text)}</div>`);
  }
  const items = unique.items || [];
  if (items.length) {
    parts.push(
      `<div class="hint" style="margin:0">${items
        .map((row) => `${escapeHtml(row.label)} ${escapeHtml(PLUS(row.value))}`)
        .join(" · ")}</div>`,
    );
  }
  if (unique.conditional) {
    parts.push(
      `<div class="hint" style="margin:6px 0 0">条件型加成：主数据只给出条件与效果名，没有可换算的数值。</div>`,
    );
  }
  return `${parts.join("")}<div class="hint" style="margin:6px 0 0">解锁条件：${escapeHtml(
    unlockText(unique),
  )}</div>`;
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
            `<h3 style="margin-top:16px">突破效果</h3>`,
            breakthroughTable(escapeHtml, item.breakthroughs),
            `<p class="hint">每列是「突破到该档并升到该档等级上限」时的数值，— 表示此时还没有这一项；加粗的是从上一档起有变化的数值。${
              item.effect_source
                ? `效果名称取自 ${escapeHtml(item.effect_source)}；字典里没有的类型只显示编号。`
                : ""
            }</p>`,
            `<h3 style="margin-top:16px">固有加成</h3>`,
            uniqueBlock(escapeHtml, item.unique),
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
