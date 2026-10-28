const API = "/api/skills";

function num(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

// 导入时间以 UTC 入库，必须按系统时区换算后再显示，不能直接截字符串
function shortTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad2 = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  );
}

function rawName(value) {
  // 主数据没有 ability_type / target_type 的字典表，只回显编号
  return value === null || value === undefined ? "—" : `原始值 ${value}`;
}

function conditionCell(slot) {
  const rows = [];
  if (slot.precondition) rows.push(`<div class="mono">前置 ${slot.precondition}</div>`);
  if (slot.condition) rows.push(`<div class="mono">发动 ${slot.condition}</div>`);
  return rows.length ? rows.join("") : `<span class="hint">无条件表达式</span>`;
}

function effectRows(slot) {
  if (!slot.effects.length) return `<tr><td colspan="7" class="hint">该槽位没有效果参数</td></tr>`;
  return slot.effects
    .map(
      (effect) => `<tr>
        <td class="mono">${effect.seq}</td>
        <td class="mono">${num(effect.ability_type)}</td>
        <td class="mono">${num(effect.value)}</td>
        <td class="mono">${num(effect.value_usage)}</td>
        <td class="mono">${num(effect.value_level_usage)}</td>
        <td class="mono">${num(effect.target_type)}</td>
        <td class="mono">${num(effect.target_value)}</td>
      </tr>`,
    )
    .join("");
}

function slotBlock(slot) {
  return `
    <div class="slot-block" style="margin-top:12px">
      <div class="toolbar" style="margin-bottom:6px">
        <span class="pill">槽位 ${slot.slot}</span>
        <span class="hint">持续时间 ${num(slot.ability_time)} · 使用方式 ${num(
          slot.ability_time_usage,
        )} · 冷却 ${num(slot.cooldown)}</span>
      </div>
      ${conditionCell(slot)}
      <table class="seg-table" style="margin-top:8px">
        <thead><tr><th>序号</th><th>效果类型</th><th>数值</th><th>数值用法</th><th>等级用法</th><th>目标类型</th><th>目标值</th></tr></thead>
        <tbody>${effectRows(slot)}</tbody>
      </table>
    </div>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, createLimiter } = ctx;
    const limiter = createLimiter(9);
    let status = null;
    let selected = null;
    let lastItems = [];

    root.innerHTML = `
      <h1 class="page-title">技能</h1>
      <p class="page-desc">条件表达式、效果参数、来源反查与进化关系全部来自本机 master.mdb。没有字典表的字段只回显原始编号 —— 宁可少给结论，也不猜含义。</p>

      <section class="card">
        <h2>数据来源</h2>
        <div class="toolbar">
          <button class="primary" id="skills-refresh">刷新技能数据</button>
          <span class="hint" id="skills-refresh-hint"></span>
        </div>
        <dl class="kv" id="skills-source"></dl>
        <div class="answer" id="skills-refresh-result" style="margin-top:12px" hidden></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>查技能</h2>
        <div class="toolbar">
          <input id="skills-query" type="search" placeholder="技能名或描述关键词，例如 加速 / 末脚" style="min-width:260px" />
          <label>分类 <select id="skills-category"><option value="">全部</option></select></label>
          <label>稀有度 <select id="skills-rarity"><option value="">全部</option></select></label>
          <label><input id="skills-source-only" type="checkbox" /> 只看能查到来源</label>
          <button id="skills-search">查询</button>
        </div>
        <div class="hint" id="skills-list-hint"></div>
        <div class="list grid" id="skills-list"></div>
      </section>

      <section class="card" style="margin-top:16px" id="skills-detail-card" hidden>
        <h2 id="skills-detail-title">技能详情</h2>
        <div id="skills-detail"></div>
      </section>
    `;

    const sourceEl = root.querySelector("#skills-source");
    const refreshHintEl = root.querySelector("#skills-refresh-hint");
    const refreshResultEl = root.querySelector("#skills-refresh-result");
    const queryEl = root.querySelector("#skills-query");
    const categoryEl = root.querySelector("#skills-category");
    const rarityEl = root.querySelector("#skills-rarity");
    const sourceOnlyEl = root.querySelector("#skills-source-only");
    const listEl = root.querySelector("#skills-list");
    const listHintEl = root.querySelector("#skills-list-hint");
    const detailCard = root.querySelector("#skills-detail-card");
    const detailTitle = root.querySelector("#skills-detail-title");
    const detailEl = root.querySelector("#skills-detail");

    function setResult(text) {
      refreshResultEl.textContent = text || "";
      refreshResultEl.hidden = !text;
    }

    function renderSource() {
      const counts = status.counts || {};
      refreshHintEl.textContent = status.mdb?.exists
        ? `master.mdb ${status.mdb.size_mb} MB`
        : "未找到 master.mdb";
      const catalog = status.catalog || null;
      sourceEl.innerHTML = `
        <dt>技能</dt><dd>${counts.skills || 0} 个 · 条件槽位 ${counts.slots || 0} 个 · 效果参数 ${
          counts.effects || 0
        } 条</dd>
        <dt>来源反查</dt><dd>马娘 ${counts.characters || 0} 条 · 协助卡提示 ${
          counts.support_cards || 0
        } 条 · 进化 / 觉醒 ${counts.upgrades || 0} 条</dd>
        <dt>导入来源</dt><dd>${escapeHtml(catalog?.source || "未导入")}${
          catalog ? ` · 导入于 ${escapeHtml(shortTime(catalog.imported_at))}` : ""
        }</dd>
        <dt>数据版本</dt><dd>${escapeHtml(status.data_version?.version || "未知")}${
          status.stale ? " · <span class=\"pill warn\">master.mdb 已更新，可刷新</span>" : ""
        }</dd>
      `;
      if (!counts.skills) {
        setResult(
          status.mdb?.exists
            ? "还没有技能数据，点「刷新技能数据」导入一次。"
            : `没有找到 ${escapeHtml(status.mdb?.path || "master.mdb")}，先在「数据同步」里导入一次。`,
        );
      }
    }

    function renderFacets() {
      const facets = status.facets || {};
      categoryEl.innerHTML =
        `<option value="">全部</option>` +
        (facets.skill_categories || [])
          .map(
            (row) =>
              `<option value="${escapeHtml(row.value)}">原始值 ${escapeHtml(
                row.value,
              )}（${row.n}）</option>`,
          )
          .join("");
      rarityEl.innerHTML =
        `<option value="">全部</option>` +
        (facets.rarities || [])
          .map(
            (row) =>
              `<option value="${escapeHtml(row.value)}">${escapeHtml(
                row.value === 6 ? "觉醒 / 剧本" : String(row.value),
              )}（${row.n}）</option>`,
          )
          .join("");
    }

    function renderList(items) {
      lastItems = items;
      if (!items.length) {
        listEl.innerHTML = `<div class="empty">没有匹配结果</div>`;
        listHintEl.textContent = "";
        return;
      }
      listHintEl.textContent = `共 ${items.length} 条（库内技能 ${status.counts?.skills || 0} 个）`;
      listEl.innerHTML = `${limiter
        .slice("skills", items)
        .map(
          (item) => `
          <div class="list-row clickable" role="button" tabindex="0" data-skill="${escapeHtml(
            item.skill_id,
          )}" aria-label="查看 ${escapeHtml(item.name)} 的详情">
            <div>
              <div class="name">
                <span class="pill">${escapeHtml(item.rarity_label || "—")}</span>
                ${escapeHtml(item.name)}
              </div>
              <div class="meta clamp">
                分类 ${escapeHtml(rawName(item.skill_category))} · 马娘 ${
                  item.character_count
                } 个 · 协助卡 ${item.support_card_count} 张${
                  item.description
                    ? `<br />${escapeHtml(item.description.replace(/\n/g, " / "))}`
                    : ""
                }
              </div>
            </div>
            <span class="mono">#${escapeHtml(item.skill_id)}</span>
          </div>`,
        )
        .join("")}${limiter.more("skills", items.length)}`;
    }

    function renderDetail(detail) {
      const skill = detail.skill;
      detailTitle.textContent = `${skill.name} · #${skill.skill_id}`;
      const attributes = (detail.attributes || [])
        .map(
          (row) =>
            `<span class="attr"><span class="attr-key">${escapeHtml(row.label)}</span>${escapeHtml(
              row.value,
            )}</span>`,
        )
        .join("");

      const slots = (detail.slots || []).length
        ? detail.slots.map(slotBlock).join("")
        : `<div class="empty">这个技能没有条件与效果参数（多见于占位或纯演出技能）</div>`;

      const characters = detail.characters || [];
      const hints = detail.support_cards?.hint || [];
      const upgrades = detail.upgrades || {};

      const evolution = [];
      for (const entry of upgrades.evolved_from || []) {
        const before = entry.base_skill_id
          ? `${escapeHtml(entry.base_name || "")}（#${entry.base_skill_id}）`
          : "未记录进化前技能";
        const extra =
          entry.source === "awakening"
            ? ` · ${escapeHtml(entry.card_title || "")} 觉醒 ${num(entry.rank)}`
            : ` · 剧本 ${num(entry.scenario_id)}`;
        evolution.push(`<li>${escapeHtml(entry.source_label)}：${before} → 本技能${extra}</li>`);
      }
      for (const entry of upgrades.evolves_to || []) {
        evolution.push(
          `<li>${escapeHtml(entry.source_label)}：本技能 → #${entry.skill_id}${
            entry.card_title ? ` · ${escapeHtml(entry.card_title)}` : ""
          }${entry.rank ? ` 觉醒 ${num(entry.rank)}` : ""}</li>`,
        );
      }

      detailEl.innerHTML = `
        <div class="attrs">${attributes}</div>
        ${
          skill.description
            ? `<pre class="detail-desc">${escapeHtml(skill.description)}</pre>`
            : ""
        }

        <h3 style="margin-top:16px">发动条件与效果参数</h3>
        <p class="hint" style="margin-top:0">条件是与游戏引擎一致的表达式；持续时间与冷却没有官方单位说明，按原始值显示。</p>
        ${slots}

        <h3 style="margin-top:16px">哪些马娘有这个技能（${characters.length}）</h3>
        ${
          characters.length
            ? `<div class="list">${characters
                .map(
                  (item) => `<div class="list-row">
                    <div>
                      <div class="name">${escapeHtml(item.name || "")} · ${escapeHtml(
                        item.title || "",
                      )}</div>
                      <div class="meta">${escapeHtml(item.need_rank_label || "—")} 可用</div>
                    </div>
                    <span class="mono">#${escapeHtml(item.card_id)}</span>
                  </div>`,
                )
                .join("")}</div>`
            : `<div class="empty">没有马娘把这个技能列在 available_skill_set 里</div>`
        }

        <h3 style="margin-top:16px">哪些协助卡给这个技能（${hints.length}）</h3>
        ${
          hints.length
            ? `<div class="list">${hints
                .map(
                  (item) => `<div class="list-row">
                    <div>
                      <div class="name">${escapeHtml(item.name || "")} · ${escapeHtml(
                        item.title || "",
                      )}</div>
                      <div class="meta">提示组 ${num(item.hint_group)}</div>
                    </div>
                    <span class="mono">#${escapeHtml(item.support_card_id)}</span>
                  </div>`,
                )
                .join("")}</div>`
            : `<div class="empty">没有协助卡在 single_mode_hint_gain 里提供这个技能</div>`
        }

        <h3 style="margin-top:16px">进化 / 觉醒关系</h3>
        ${
          evolution.length
            ? `<ul class="hint" style="margin-top:0">${evolution.join("")}</ul>`
            : `<div class="empty">主数据里没有这个技能的进化记录</div>`
        }

        <h3 style="margin-top:16px">口径说明</h3>
        <ul class="hint" style="margin-top:0">
          ${(detail.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("")}
        </ul>
      `;
      detailCard.hidden = false;
      detailCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    async function loadStatus() {
      status = await api(`${API}/status`);
      renderSource();
      renderFacets();
    }

    async function search() {
      const params = new URLSearchParams({ limit: "100" });
      const query = queryEl.value.trim();
      if (query) params.set("q", query);
      if (categoryEl.value) params.set("skill_category", categoryEl.value);
      if (rarityEl.value) params.set("rarity", rarityEl.value);
      if (sourceOnlyEl.checked) params.set("with_source_only", "true");
      try {
        const result = await api(`${API}/skills?${params.toString()}`);
        renderList(result.items || []);
      } catch (error) {
        toast(error.message, "error");
      }
    }

    async function openDetail(skillId) {
      try {
        selected = await api(`${API}/skills/${skillId}`);
        renderDetail(selected);
      } catch (error) {
        toast(error.message, "error");
      }
    }

    async function refresh() {
      try {
        setResult("正在从 master.mdb 导入技能数据…");
        const result = await api(`${API}/refresh?force=true`, { method: "POST" });
        if (result.imported) {
          const summary = result.summary || {};
          setResult(
            `已导入：技能 ${summary.skills} 个 · 效果参数 ${summary.effects} 条 · 马娘来源 ${
              summary.characters
            } 条 · 协助卡来源 ${summary.support_cards} 条 · 进化 ${summary.upgrades} 条` +
              (Object.keys(summary.skipped || {}).length
                ? `（跳过：${Object.values(summary.skipped).join("；")}）`
                : ""),
          );
        } else {
          setResult(result.reason || "本地已是最新，未重复导入");
        }
        await loadStatus();
        if (selected) await openDetail(selected.skill.skill_id);
      } catch (error) {
        setResult(`导入失败：${error.message}`);
      }
    }

    function onListClick(event) {
      const row = event.target.closest(".list-row.clickable");
      if (!row) return;
      openDetail(row.dataset.skill);
    }

    function onListKeydown(event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest(".list-row.clickable");
      if (!row) return;
      event.preventDefault();
      openDetail(row.dataset.skill);
    }

    root.querySelector("#skills-refresh").addEventListener("click", refresh);
    root.querySelector("#skills-search").addEventListener("click", search);
    queryEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        search();
      }
    });
    categoryEl.addEventListener("change", search);
    rarityEl.addEventListener("change", search);
    sourceOnlyEl.addEventListener("change", search);
    listEl.addEventListener("click", onListClick);
    listEl.addEventListener("keydown", onListKeydown);
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-more]");
      if (!button || button.dataset.more !== "skills") return;
      limiter.toggle("skills");
      renderList(lastItems);
    });

    await loadStatus();
    if (status.counts?.skills) await search();

    // 从别处跳进来时带 ?skill=<id>：直接打开该技能详情（图鉴的技能行就是这么跳的）
    const wanted = ctx.params?.get("skill");
    if (wanted) {
      await openDetail(wanted);
      detailCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    return () => {
      listEl.removeEventListener("click", onListClick);
      listEl.removeEventListener("keydown", onListKeydown);
    };
  },
};
