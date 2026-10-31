const API = "/api/events";
// 日程页要展示卡池与大赛排期，它们各有自己的模块与接口；这里只调 HTTP，不跨模块 import，
// 任一模块不可用时这一段降级成提示，不影响 BWiki 时间轴。
const GACHA_API = "/api/gacha";
const RACES_API = "/api/races";

// 后端给的是带时区偏移的 ISO 字符串（抓取时间多为 UTC），必须先按系统时区换算再截断，
// 直接 slice 会把 UTC 时钟当成当地时间显示（在中国时区会差 8 小时）。
function pad2(value) {
  return String(value).padStart(2, "0");
}

function shortTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  );
}

function dateOnly(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function num(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function stateLabel(item) {
  if (item.state === "active") {
    if (item.days_left === null || item.days_left === undefined) return "进行中";
    if (item.days_left <= 0) return "今天结束";
    return `还剩 ${item.days_left} 天`;
  }
  if (item.state === "permanent") return "常驻";
  if (item.state === "upcoming") {
    const days = item.days_to_start;
    if (days === 0) return "今天开始";
    if (days === 1) return "明天开始";
    if (days > 1) return `${days} 天后开始`;
    return "即将开始";
  }
  if (item.state === "ended") return "已结束";
  return "时间未定";
}

function stateTone(item) {
  if (item.state === "active" || item.state === "permanent") return "ok";
  if (item.state === "upcoming") return "info";
  if (item.state === "ended") return "muted";
  return "warn";
}

function serverTag(item, escapeHtml) {
  return `<span class="tag server-${escapeHtml(item.server)}">${escapeHtml(
    item.server_label || item.server,
  )}</span>`;
}

function levelTag(item, escapeHtml) {
  const level = String(item.source_level || "?").toUpperCase();
  const tone = level === "A" ? "a" : level === "B" ? "b" : level === "C" ? "c" : "d";
  return `<span class="tag level-${tone}" title="${escapeHtml(item.level_label || "")}">${escapeHtml(
    level,
  )} 级</span>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, createLimiter } = ctx;
    const limiter = createLimiter(6);
    let status = null;
    let schedule = null;
    let history = null;
    let refreshing = false;

    root.innerHTML = `
      <h1 class="page-title">日程</h1>
      <p class="page-desc">一页看完所有时间相关的东西：<strong>卡池</strong>与<strong>大赛</strong>排期来自本机 master.mdb（客户端内置，含未来排期），<strong>简中服已开始的卡池</strong>来自 BWiki 分类页（带来源链接与抓取时间）。只跟简中服，不跟踪繁中服与日服。所有时间按系统时区显示。</p>
      <section class="card">
        <h2>数据来源</h2>
        <div class="toolbar" style="margin-bottom:8px">
          <button class="ghost" id="events-refresh">刷新 BWiki</button>
          <span class="hint" id="events-last"></span>
        </div>
        <details id="events-sources-details">
          <summary class="hint" id="events-sources-summary">展开来源明细</summary>
          <div class="list" id="events-sources" style="margin-top:8px"></div>
        </details>
        <div class="answer" id="events-refresh-result" style="margin-top:12px">还没有抓取过。</div>
      </section>
      <section class="card" style="margin-top:16px">
        <h2>近期提醒</h2>
        <p class="hint" style="margin-top:0">按系统时区算：<strong>即将开始</strong>是未来开始的条目，<strong>即将结束</strong>是剩余天数不超过阈值的进行中条目。BWiki 的分类页只收录已经开始的卡池，所以「即将开始」经常是空的 —— 那就看「即将结束」，它才是接下来会变的东西。</p>
        <div id="events-near"></div>
      </section>
      <section class="card" style="margin-top:16px">
        <h2>卡池（master.mdb 排期）</h2>
        <p class="hint" style="margin-top:0">来自客户端内置排期，含还没开放的未来卡池，所以能提前看到下一期精选；实际开放时间以游戏内公告为准。BWiki 抓到的简中服已开始卡池仍在下方时间轴。</p>
        <div class="toolbar">
          <button class="ghost" id="gacha-refresh">刷新卡池排期</button>
          <label><input id="gacha-ended" type="checkbox" /> 显示最近已结束</label>
          <span class="hint" id="gacha-hint"></span>
        </div>
        <div id="gacha-body"></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>大赛（群英联赛）</h2>
        <p class="hint" style="margin-top:0">排期、轮次窗口与晋级门槛来自 master.mdb 的 <code>champions_*</code>；天气与马场是该届的固定值，不是概率。时间按系统时区显示。</p>
        <div class="toolbar">
          <label><input id="champions-upcoming" type="checkbox" checked /> 只看未结束</label>
          <span class="hint" id="champions-hint"></span>
        </div>
        <div class="list" id="champions-list"></div>
        <div id="champion-detail" style="margin-top:12px"></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>时间轴（BWiki 简中服卡池）</h2>
        <div class="toolbar" style="margin-bottom:8px">
          <label><input id="events-placeholders" type="checkbox" /> 显示占位条目</label>
          <span class="hint" id="events-placeholder-hint"></span>
        </div>
        <div class="hint" id="events-window"></div>
        <div id="events-timeline"></div>
      </section>
      <section class="card" style="margin-top:16px">
        <h2>最近变更</h2>
        <div class="hint">只有内容真的变了才会记录，没有变化时这里是空的。</div>
        <div class="list" id="events-changes"></div>
      </section>
    `;

    const refreshButton = root.querySelector("#events-refresh");
    const lastEl = root.querySelector("#events-last");
    const sourcesEl = root.querySelector("#events-sources");
    const sourcesSummaryEl = root.querySelector("#events-sources-summary");
    const resultEl = root.querySelector("#events-refresh-result");
    const windowEl = root.querySelector("#events-window");
    const timelineEl = root.querySelector("#events-timeline");
    const nearEl = root.querySelector("#events-near");
    const placeholdersEl = root.querySelector("#events-placeholders");
    const placeholderHintEl = root.querySelector("#events-placeholder-hint");
    const gachaBodyEl = root.querySelector("#gacha-body");
    const gachaHintEl = root.querySelector("#gacha-hint");
    const gachaEndedEl = root.querySelector("#gacha-ended");
    const gachaRefreshButton = root.querySelector("#gacha-refresh");
    const championsEl = root.querySelector("#champions-list");
    const championsHintEl = root.querySelector("#champions-hint");
    const championsUpcomingEl = root.querySelector("#champions-upcoming");
    const championDetailEl = root.querySelector("#champion-detail");
    let gacha = null;
    let gachaStatus = null;
    let champions = null;
    const changesEl = root.querySelector("#events-changes");

    function scheduleUrl() {
      const params = new URLSearchParams();
      if (placeholdersEl.checked) params.set("include_placeholders", "true");
      const query = params.toString();
      return query ? `${API}/schedule?${query}` : `${API}/schedule`;
    }

    function renderStatus() {
      lastEl.textContent = status?.last_refresh
        ? `上次刷新 ${shortTime(status.last_refresh)}`
        : "还没有刷新过";
      const items = status?.items || {};
      const byKind = items.by_kind || {};
      const sources = status?.sources || [];
      sourcesSummaryEl.textContent = `展开来源明细（${sources.length} 条 · 简中服卡池）`;
      sourcesEl.innerHTML = sources
        .map((source) => {
          const lastFetch = (status.last_fetches || []).find((row) => row.source_key === source.key);
          const detail = lastFetch
            ? `${shortTime(lastFetch.finished_at || lastFetch.started_at)} · 条目 ${lastFetch.items}` +
              (lastFetch.error ? ` · 失败：${lastFetch.error}` : "") +
              (lastFetch.stale ? " · 使用缓存" : "")
            : "未抓取";
          return `
            <div class="list-row">
              <div>
                <div class="name">${escapeHtml(source.label)}
                  <span class="tag server-${escapeHtml(source.server)}">${escapeHtml(
                    source.server_label,
                  )}</span>
                  <span class="tag level-${String(source.source_level).toLowerCase()}">${escapeHtml(
                    source.source_level,
                  )} 级</span>
                  ${source.enabled ? "" : `<span class="tag level-d">未启用</span>`}
                </div>
                <div class="meta">分类:${escapeHtml(source.category)} · ${escapeHtml(detail)}</div>
              </div>
              <span class="pill ${lastFetch?.error ? "error" : "ok"}">${source.items}</span>
            </div>`;
        })
        .join("");
      if (!sourcesEl.innerHTML) {
        sourcesEl.innerHTML = `<div class="empty">没有配置来源</div>`;
      }
      if (!items.total) {
        resultEl.textContent = "还没有数据，点「刷新 BWiki」抓取一次。";
      } else {
        const parts = [];
        if (byKind.pool) parts.push(`卡池 ${byKind.pool}`);
        if (byKind.event) parts.push(`活动 ${byKind.event}`);
        resultEl.textContent = `库里共 ${items.total} 条${parts.length ? `：${parts.join(" · ")}` : ""}。`;
      }
    }

    function row(item) {
      const flags = [
        item.starts_today ? `<span class="tag flag-today">今天</span>` : "",
        !item.starts_today && item.this_week ? `<span class="tag flag-week">本周</span>` : "",
      ]
        .filter(Boolean)
        .join(" ");
      const span = item.ends_local
        ? `<span>${escapeHtml(item.starts_local || "常驻")}</span><br /><span>→ ${escapeHtml(
            item.ends_local,
          )}</span>`
        : `<span>${escapeHtml(item.starts_local || (item.state === "permanent" ? "常驻" : "时间未定"))}</span>`;
      // 来源服时区与本机不同时（如日服 JST），补一行原始时间，避免「12:00 变成 11:00」看不出原因
      const sourceSpan = !item.same_zone && (item.starts_source || item.ends_source)
        ? `${item.starts_source || "常驻"}${item.ends_source ? ` → ${item.ends_source}` : ""}`
        : "";
      const ids = Object.entries(item.ids || {})
        .map(([key, values]) => `${escapeHtml(key)} ${values.length}`)
        .join(" · ");
      const link = item.source_url
        ? `<a class="source-link" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">原页</a>`
        : "";
      return `
        <div class="tl-row ${item.starts_today ? "today" : item.this_week ? "week" : ""}">
          <div class="tl-time mono">${span}<br /><span class="meta">${escapeHtml(
            item.zone || "",
          )}</span></div>
          <div class="tl-body">
            <div class="name">${flags}${escapeHtml(item.title)}</div>
            <div class="meta">
              <span class="pill ${stateTone(item)}">${escapeHtml(stateLabel(item))}</span>
              ${serverTag(item, escapeHtml)}
              ${levelTag(item, escapeHtml)}
              <span>${escapeHtml(item.kind_label || item.kind)}</span>
              ${item.category ? `<span>· ${escapeHtml(item.category)}</span>` : ""}
              ${ids ? `<span>· ${ids}</span>` : ""}
            </div>
            ${
              sourceSpan
                ? `<div class="meta"><span class="tag">来源时区 ${escapeHtml(
                    item.source_zone || "",
                  )} ${escapeHtml(sourceSpan)}</span></div>`
                : ""
            }
            <div class="meta">${escapeHtml(item.source_label || item.source_key)} · 抓取于 ${escapeHtml(
              shortTime(item.fetched_at),
            )} ${link}</div>
          </div>
        </div>`;
    }

    function renderSchedule() {
      renderNear();
      const items = schedule?.items || [];
      const counts = schedule?.counts || {};
      const hidden = counts.placeholders || 0;
      placeholderHintEl.textContent = hidden
        ? `已隐藏 ${hidden} 条来源占位行`
        : placeholdersEl.checked
          ? "占位行已显示（来源页面还没填内容，标记为 D 级）"
          : "没有占位行";
      windowEl.textContent = items.length
        ? `共 ${items.length} 条 · 进行中 ${counts.active || 0} · 即将开始 ${
            counts.upcoming || 0
          } · 已结束 ${counts.ended || 0} · 今天/本周开始 ${counts.today || 0}/${
            counts.this_week || 0
          }${hidden ? ` · 已隐藏占位 ${hidden}` : ""}`
        : "还没有抓到卡池，先点上面的「刷新 BWiki」。";
      if (!items.length) {
        timelineEl.innerHTML = `<div class="empty">先在上面的「数据来源」里点一次刷新。</div>`;
        return;
      }
      const groups = [
        { key: "active", title: "正在进行", match: (item) => ["active", "permanent"].includes(item.state) },
        { key: "upcoming", title: "即将开始", match: (item) => item.state === "upcoming" },
        { key: "unknown", title: "时间未定", match: (item) => item.state === "unknown" },
        { key: "ended", title: "已结束", match: (item) => item.state === "ended" },
      ];
      timelineEl.innerHTML = groups
        .map((group) => {
          const rows = items.filter(group.match);
          if (!rows.length) {
            // 「即将开始」即使为空也要出现：否则看不出是「没有」还是「没渲染」
            if (group.key !== "upcoming") return "";
            const near = schedule?.near_term;
            return `<h3>${group.title}（0）</h3><div class="empty">没有未来开始的条目${
              near?.ending_soon_total
                ? `；${near.horizon_days} 天内结束的有 ${near.ending_soon_total} 条，见上方「近期提醒」`
                : ""
            }。</div>`;
          }
          return `<h3>${group.title}（${rows.length}）</h3><div class="timeline">${limiter
            .slice(`tl:${group.key}`, rows)
            .map(row)
            .join("")}</div>${limiter.more(`tl:${group.key}`, rows.length)}`;
        })
        .join("");
    }

    function nearRow(item, kind) {
      const days = kind === "upcoming" ? item.days_to_start : item.days_left;
      const when =
        kind === "upcoming"
          ? `还有 ${Math.max(days ?? 0, 0)} 天开始 · ${item.starts_local || "时间未定"}`
          : `还剩 ${Math.max(days ?? 0, 0)} 天 · 到 ${item.ends_local || "时间未定"}`;
      // D 级多是来源里的占位行，保留但不给「提醒」的分量
      const placeholder = String(item.source_level || "").toUpperCase() === "D";
      return `
        <div class="list-row">
          <div>
            <div class="name">${escapeHtml(item.title)}</div>
            <div class="meta">
              ${serverTag(item, escapeHtml)}
              ${levelTag(item, escapeHtml)}
              <span>${escapeHtml(item.kind_label || item.kind)}</span>
              <span>· ${escapeHtml(when)}</span>
              ${placeholder ? `<span class="tag level-d">占位</span>` : ""}
            </div>
          </div>
          <span class="pill ${kind === "upcoming" ? "ok" : placeholder ? "muted" : "warn"}">${
            kind === "upcoming" ? "未开始" : "将结束"
          }</span>
        </div>`;
    }

    function renderNear() {
      const near = schedule?.near_term;
      if (!near) {
        nearEl.innerHTML = `<div class="empty">还没有日程数据。</div>`;
        return;
      }
      const upcoming = near.upcoming || [];
      const ending = near.ending_soon || [];
      const upcomingBlock = upcoming.length
        ? `<div class="list">${upcoming.map((item) => nearRow(item, "upcoming")).join("")}</div>`
        : `<div class="empty">库里没有未来开始的条目：BWiki 分类页只收录已经开始的卡池，下一期要等页面更新后再刷新。未来卡池见下方「卡池」卡（读 master.mdb 的内置排期）。</div>`;
      const endingBlock = ending.length
        ? `<div class="list">${ending.map((item) => nearRow(item, "ending")).join("")}</div>`
        : `<div class="empty">${near.horizon_days} 天内没有条目结束。</div>`;
      nearEl.innerHTML = `
        <h3>即将开始（${near.upcoming_total || 0}）</h3>${upcomingBlock}
        <h3 style="margin-top:14px">${near.horizon_days} 天内结束（${
          near.ending_soon_total || 0
        }）</h3>${endingBlock}`;
    }

    // ---------------------------------------------------------------- 卡池
    // 「+N」这种折叠标签要能点开：按卡池 id 记住展开状态，重渲染时保留
    const expandedPickups = new Set();

    function pickupTags(item) {
      const all = item.pickups || [];
      if (!all.length) return `<span class="tag muted">无精选卡片</span>`;
      const key = String(item.gacha_id);
      const open = expandedPickups.has(key);
      const shown = open ? all : all.slice(0, 4);
      const tags = shown
        .map(
          (row) =>
            `<span class="tag">${escapeHtml(row.card_type_label)} · ${escapeHtml(
              row.name || `未收录 #${row.card_id}`,
            )}${row.title ? ` ${escapeHtml(row.title)}` : ""}</span>`,
        )
        .join(" ");
      const hiddenCount = all.length - shown.length;
      // 大杂烩池（必得、新春之类）精选几十张，默认只列 4 张，剩下的折成可点的「+N」
      const toggle = open
        ? ` <button type="button" class="tag muted" data-pickups="${escapeHtml(
            key,
          )}" aria-expanded="true">收起</button>`
        : hiddenCount > 0
          ? ` <button type="button" class="tag muted" data-pickups="${escapeHtml(
              key,
            )}" aria-expanded="false" aria-label="展开其余 ${hiddenCount} 张精选">+${hiddenCount}</button>`
          : "";
      return tags + toggle;
    }

    function gachaRow(item) {
      const tone = item.permanent
        ? "info"
        : item.state === "upcoming"
          ? "info"
          : item.state === "unscheduled"
            ? "muted"
            : item.state === "ended"
              ? "muted"
              : "ok";
      const label = item.permanent
        ? "常驻"
        : item.state === "unscheduled"
          ? "未定档"
          : item.state === "ended"
            ? "已结束"
            : item.state === "upcoming"
              ? `还有 ${Math.max(item.days_to_start ?? 0, 0)} 天开始`
              : `还剩 ${Math.max(item.days_left ?? 0, 0)} 天`;
      const span = item.permanent
        ? `${shortTime(item.starts_local)} 起 · 长期开放`
        : item.state === "unscheduled"
          ? `记录时间 ${shortTime(item.starts_local)} → ${shortTime(item.ends_local)}（客户端占位）`
          : `${shortTime(item.starts_local)} → ${shortTime(item.ends_local)}`;
      const pickups = pickupTags(item);
      return `
        <div class="list-row">
          <div>
            <div class="name">${escapeHtml(item.title)}</div>
            <div class="meta">
              <span class="pill ${tone}">${escapeHtml(label)}</span>
              <span class="mono nowrap">${escapeHtml(span)}</span>
              <span class="tag">${escapeHtml(item.type_label)}</span>
            </div>
            <div class="meta">${pickups}</div>
          </div>
          <span class="mono">#${escapeHtml(item.gacha_id)}</span>
        </div>`;
    }

    function renderGacha() {
      if (!gacha) {
        gachaBodyEl.innerHTML = `<div class="empty">卡池数据不可用（/api/gacha 未响应）。</div>`;
        return;
      }
      const counts = gacha.counts || {};
      const importedAt = gachaStatus?.catalog?.imported_at
        ? ` · 排期导入于 ${shortTime(gachaStatus.catalog.imported_at)}`
        : "";
      gachaHintEl.textContent = `未结束 ${(counts.active || 0) + (counts.upcoming || 0)} 个 · 时间按 ${
        gacha.zone || "系统时区"
      }${counts.unscheduled ? ` · ${counts.unscheduled} 个未定档占位` : ""}${importedAt}`;
      const upcoming = gacha.upcoming || [];
      const active = gacha.active || [];
      const unscheduled = gacha.unscheduled || [];
      const ended = gacha.ended || [];

      const section = (key, title, items, emptyText) => `
        <h3 style="margin-top:14px">${title}（${items.length}）</h3>
        ${
          items.length
            ? `<div class="list grid">${limiter.slice(key, items).map(gachaRow).join("")}</div>${limiter.more(
                key,
                items.length,
              )}`
            : `<div class="empty">${emptyText}</div>`
        }`;

      const blocks = [
        section(
          "gacha:upcoming",
          "即将开始",
          upcoming,
          "当前客户端排期里没有未来卡池（客户端没更新时不会提前出现）。",
        ),
        section("gacha:active", "进行中", active, "当前没有进行中的卡池"),
      ];
      if (unscheduled.length) {
        blocks.push(
          `<div class="hint" style="margin-top:12px">未定档：日期是客户端占位值（2049 / 2050 年），不是真实排期。</div>`,
          section("gacha:unscheduled", "未定档", unscheduled, ""),
        );
      }
      if (gachaEndedEl.checked) {
        blocks.push(section("gacha:ended", "最近已结束", ended, "没有历史记录"));
      }
      gachaBodyEl.innerHTML = blocks.join("");
    }

    async function loadGacha() {
      try {
        const [list, info] = await Promise.all([
          api(`${GACHA_API}/banners?limit_ended=10`),
          api(`${GACHA_API}/status`).catch(() => null),
        ]);
        gacha = list;
        gachaStatus = info;
        renderGacha();
      } catch (error) {
        gacha = null;
        gachaBodyEl.innerHTML = `<div class="empty">卡池数据不可用：${escapeHtml(
          error.message,
        )}</div>`;
      }
    }

    // ---------------------------------------------------------------- 大赛
    const CHAMPION_STATE = { upcoming: "未开始", running: "进行中", finished: "已结束" };
    const CHAMPION_STATE_CLASS = { upcoming: "info", running: "ok", finished: "muted" };

    function championRow(item) {
      return `
        <div class="list-row clickable" data-champion="${item.champions_id}" tabindex="0">
          <div>
            <div class="name">${escapeHtml(item.name)}</div>
            <div class="meta">${escapeHtml(shortTime(item.starts_at))} → ${escapeHtml(
              shortTime(item.ends_at),
            )} · ${escapeHtml(item.course_label || "赛道未知")} · ${escapeHtml(
              item.weather_label || "—",
            )} / ${escapeHtml(item.ground_label || "—")}</div>
          </div>
          <span class="pill ${CHAMPION_STATE_CLASS[item.state] || "muted"}">${escapeHtml(
            CHAMPION_STATE[item.state] || item.state,
          )}</span>
        </div>`;
    }

    function renderChampions() {
      const items = champions?.items || [];
      championsHintEl.textContent = items.length
        ? `显示 ${items.length} 届 · 共 ${champions.counts?.total || items.length} 届 · 时间按 ${
            champions.zone || "系统时区"
          } 显示`
        : "没有大赛排期，先刷新一次数据。";
      championsEl.innerHTML = items.length
        ? items.map(championRow).join("")
        : `<div class="empty">master.mdb 里没有 champions_* 排期。</div>`;
    }

    function championDetail(item) {
      if (!item) return "";
      const finalLabel = (round) => (round.round === 3 ? `决赛第 ${round.round_detail} 场` : "—");
      const roundRows = (item.rounds || [])
        .map(
          (round) => `<tr>
            <td>${escapeHtml(round.round_label || `第 ${round.round + 1} 阶段`)}</td>
            <td>${escapeHtml(finalLabel(round))}</td>
            <td class="mono">${escapeHtml(shortTime(round.starts_at))}</td>
            <td class="mono">${escapeHtml(shortTime(round.ends_at))}</td>
          </tr>`,
        )
        .join("");
      const leagueBlocks = (item.leagues || [])
        .map((league) => {
          const rows = league.entries
            .map(
              (entry) => `<tr>
                <td class="mono">${entry.round_id}</td>
                <td class="mono">${num(entry.tier)}</td>
                <td>${escapeHtml(entry.requirement || "—")}${
                  entry.wins_needed_2 ? ` + 附加 ${entry.wins_needed_2} 胜` : ""
                }</td>
                <td class="mono">${num(entry.free_entry_number)}</td>
              </tr>`,
            )
            .join("");
          return `<h4 style="margin-top:12px">${escapeHtml(league.label || "联赛")}</h4>
            <table class="seg-table">
              <thead><tr><th>阶段编号</th><th>组别</th><th>晋级门槛</th><th>免预选名额</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>`;
        })
        .join("");
      return `
        <dl class="kv">
          <dt>名称</dt><dd>${escapeHtml(item.name)}</dd>
          <dt>起止（${escapeHtml(champions?.zone || "系统时区")}）</dt><dd>${escapeHtml(
            shortTime(item.starts_at),
          )} → ${escapeHtml(shortTime(item.ends_at))}</dd>
          <dt>赛道</dt><dd>${escapeHtml(item.course_label || "未知")} · ${escapeHtml(
            item.course_id || "—",
          )}${item.course_count > 1 ? `（预赛与决赛共 ${item.course_count} 条赛道）` : ""}</dd>
          <dt>天气 / 马场</dt><dd>${escapeHtml(item.weather_label || "—")} / ${escapeHtml(
            item.ground_label || "—",
          )} · 该届固定值</dd>
        </dl>
        <h3 style="margin-top:16px">轮次窗口</h3>
        <table class="seg-table">
          <thead><tr><th>阶段</th><th>决赛场次</th><th>开始</th><th>结束</th></tr></thead>
          <tbody>${roundRows}</tbody>
        </table>
        <h3 style="margin-top:16px">晋级门槛</h3>
        ${leagueBlocks}
        <p class="hint">「阶段编号」是 master.mdb 的 <code>round_id</code> 原值，mdb 没有编号与阶段名称的字典，所以按原样列出。赛道分段的详细几何在「赛道」页。</p>
      `;
    }

    async function loadChampions() {
      const params = new URLSearchParams();
      if (championsUpcomingEl.checked) params.set("upcoming", "true");
      try {
        champions = await api(`${RACES_API}/champions?${params.toString()}`);
        renderChampions();
      } catch (error) {
        champions = null;
        championsHintEl.textContent = "";
        championsEl.innerHTML = `<div class="empty">大赛数据不可用：${escapeHtml(
          error.message,
        )}</div>`;
      }
    }

    async function openChampion(championsId) {
      try {
        const item = await api(`${RACES_API}/champions/${encodeURIComponent(championsId)}`);
        championDetailEl.innerHTML = championDetail(item);
        championDetailEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (error) {
        toast(error.message, "error");
      }
    }

    function renderChanges() {
      const changes = history?.changes || [];
      if (!changes.length) {
        changesEl.innerHTML = `<div class="empty">没有变更记录</div>`;
        return;
      }
      const actionLabel = { added: "新增", changed: "变更", removed: "移除" };
      const rows = changes.slice(0, 20);
      changesEl.innerHTML = `${limiter
        .slice("chg", rows)
        .map((change) => {
          const detail =
            change.action === "changed" && change.field
              ? `${change.field}: ${change.before_value ?? "空"} → ${change.after_value ?? "空"}`
              : escapeHtml(change.title || change.uid);
          return `
            <div class="list-row">
              <div>
                <div class="name"><span class="pill ${
                  change.action === "added" ? "ok" : change.action === "removed" ? "error" : "info"
                }">${actionLabel[change.action] || change.action}</span> ${detail}</div>
                <div class="meta">${escapeHtml(shortTime(change.changed_at))} · ${escapeHtml(
                  change.source_key,
                )}</div>
              </div>
            </div>`;
        })
        .join("")}${limiter.more("chg", rows.length)}`;
    }

    function renderRefreshResult(result) {
      if (!result) return;
      const totals = result.totals || {};
      const lines = [
        `新增 ${totals.added || 0} · 变更 ${totals.changed || 0} · 移除 ${totals.removed || 0}`,
      ];
      (result.results || []).forEach((row) => {
        if (row.ok) {
          lines.push(
            `${row.label}：${row.items} 条（新增 ${row.added} / 变更 ${row.changed} / 移除 ${row.removed}）${
              row.cached ? " · 304 复用缓存" : ""
            }${row.stale ? " · 抓取失败，用缓存" : ""}`,
          );
        } else {
          lines.push(`${row.label}：失败 — ${row.error}`);
        }
      });
      (result.results || [])
        .filter((row) => row.note)
        .forEach((row) => lines.push(`· ${row.note}`));
      resultEl.textContent = lines.join("\n");
    }

    async function loadSchedule() {
      schedule = await api(scheduleUrl());
      renderSchedule();
    }

    async function load() {
      status = await api(`${API}/status`);
      renderStatus();
      history = await api(`${API}/history?limit=50`);
      renderChanges();
      await loadSchedule();
      // 卡池与大赛来自各自模块，失败时只影响自己那一段
      await Promise.all([loadGacha(), loadChampions()]);
    }

    placeholdersEl.addEventListener("change", async () => {
      try {
        await loadSchedule();
      } catch (error) {
        windowEl.textContent = `加载失败：${error.message}`;
      }
    });

    refreshButton.addEventListener("click", async () => {
      if (refreshing) return;
      refreshing = true;
      refreshButton.disabled = true;
      refreshButton.textContent = "抓取中…";
      resultEl.textContent = `正在按 ${status?.interval_seconds ?? 1.5} 秒间隔依次抓取，请稍候。`;
      try {
        const result = await api(`${API}/refresh`, {
          method: "POST",
          body: JSON.stringify({ sources: null, force: true }),
        });
        // 先重载页面数据，再写刷新摘要——renderStatus 会重置这段提示文案
        await load();
        renderRefreshResult(result);
        const totals = result.totals || {};
        toast(
          `刷新完成：新增 ${totals.added || 0} · 变更 ${totals.changed || 0} · 移除 ${
            totals.removed || 0
          }`,
          result.ok ? "info" : "error",
        );
      } catch (error) {
        resultEl.textContent = `刷新失败：${error.message}`;
        toast(error.message, "error");
      } finally {
        refreshing = false;
        refreshButton.disabled = false;
        refreshButton.textContent = "刷新 BWiki";
      }
    });

    gachaEndedEl.addEventListener("change", renderGacha);

    // 点「+N」看这张卡池的全部精选，再点「收起」
    gachaBodyEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-pickups]");
      if (!button) return;
      const key = button.dataset.pickups;
      if (expandedPickups.has(key)) expandedPickups.delete(key);
      else expandedPickups.add(key);
      renderGacha();
    });

    // 长列表的「展开其余 N 条」：由根节点代理，各渲染函数只负责切片与按钮
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-more]");
      if (!button) return;
      const key = button.dataset.more;
      limiter.toggle(key);
      if (key.startsWith("gacha:")) renderGacha();
      else if (key.startsWith("tl:")) renderSchedule();
      else if (key === "chg") renderChanges();
    });

    gachaRefreshButton.addEventListener("click", async () => {
      gachaRefreshButton.disabled = true;
      gachaHintEl.textContent = "正在从 master.mdb 导入卡池排期…";
      try {
        const result = await api(`${GACHA_API}/refresh?force=true`, { method: "POST" });
        if (result.imported) {
          const summary = result.summary || {};
          toast(
            `卡池排期已导入：${summary.banners} 个（未来 ${summary.upcoming} 个）`,
            "info",
          );
        } else {
          toast(result.reason || "本地已是最新", "info");
        }
        await loadGacha();
      } catch (error) {
        gachaHintEl.textContent = `导入失败：${error.message}`;
        toast(error.message, "error");
      } finally {
        gachaRefreshButton.disabled = false;
      }
    });

    championsEl.addEventListener("click", (event) => {
      const row = event.target.closest("[data-champion]");
      if (row) openChampion(row.dataset.champion);
    });

    championsEl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("[data-champion]");
      if (!row) return;
      event.preventDefault();
      openChampion(row.dataset.champion);
    });

    championsUpcomingEl.addEventListener("change", async () => {
      championDetailEl.innerHTML = "";
      await loadChampions();
    });

    try {
      await load();
    } catch (error) {
      resultEl.textContent = `加载失败：${error.message}`;
    }
  },
};
