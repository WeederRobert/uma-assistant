const API = "/api/races";

// 时间戳多为 UTC，必须按系统时区换算后再截断，否则会比本地时间慢 8 小时
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

function num(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function severity(item) {
  if (!item.has_geometry) return `<span class="pill muted">无几何</span>`;
  if ((item.warnings || []).length) return `<span class="pill warn">有提示</span>`;
  return `<span class="pill ok">几何齐全</span>`;
}

function segmentRows(segments) {
  if (!segments.length) return `<div class="empty">数据页没有分段记录</div>`;
  const rows = segments
    .map((segment) => {
      const straight = segment.type === "直线" || segment.type === "直";
      const final = segment.name === "终直";
      const label = segment.inferred
        ? `${segment.name || segment.type} <span class="pill muted">推断</span>`
        : segment.name || segment.type;
      return `<tr class="${final ? "final" : straight ? "straight" : ""} ${
        segment.inferred ? "inferred" : ""
      }">
        <td>${label}</td>
        <td>${segment.type}</td>
        <td class="mono">${num(segment.start)}</td>
        <td class="mono">${num(segment.end)}</td>
        <td class="mono">${num(segment.length)}</td>
      </tr>`;
    })
    .join("");
  return `<table class="seg-table">
    <thead><tr><th>名称</th><th>类型</th><th>起点</th><th>终点</th><th>长度</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function slopeRows(slopes) {
  if (!slopes.length) return `<div class="hint">数据页没有坡道记录。</div>`;
  const rows = slopes
    .map(
      (slope) => `<tr>
        <td class="mono">${num(slope.start)}</td>
        <td class="mono">${num(slope.end)}</td>
        <td class="mono">${num(slope.length)}</td>
        <td class="mono">${slope.slope > 0 ? "+1" : "-1"}</td>
      </tr>`,
    )
    .join("");
  return `<table class="seg-table">
    <thead><tr><th>起点</th><th>终点</th><th>长度</th><th>标号</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, createLimiter } = ctx;
    const limiter = createLimiter(9);
    let status = null;
    let courses = null;
    let races = null;
    let conditions = null;
    let selected = null;
    let refreshing = false;

    root.innerHTML = `
      <h1 class="page-title">赛道</h1>
      <p class="page-desc">赛事目录来自 master.mdb（客户端原始数据），赛道路线、终直与坡道来自 BWiki 数据页（社区整理，字段含义为推断）。这里给出的是结构化事实，不含最高速度、加速度与天气修正公式。</p>
      <section class="card">
        <h2>数据来源</h2>
        <div class="toolbar">
          <button class="primary" id="races-refresh">刷新赛道数据</button>
          <span class="hint" id="races-refresh-hint"></span>
        </div>
        <dl class="kv" id="races-source"></dl>
        <div class="answer" id="races-refresh-result" style="margin-top:12px" hidden></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>筛选赛道</h2>
        <div class="toolbar">
          <label>赛场 <select id="f-venue"><option value="">全部</option></select></label>
          <label>场地 <select id="f-surface"><option value="">全部</option></select></label>
          <label>方向 <select id="f-direction"><option value="">全部</option></select></label>
          <label>距离 <input id="f-min" type="number" min="800" max="4000" step="100" placeholder="最短" style="width:82px" />
            – <input id="f-max" type="number" min="800" max="4000" step="100" placeholder="最长" style="width:82px" /></label>
          <label><input id="f-geometry" type="checkbox" /> 仅有几何数据</label>
          <button id="f-clear">重置</button>
        </div>
        <div class="hint" id="races-courses-count"></div>
        <div class="list grid" id="races-courses"></div>
      </section>

      <section class="card" style="margin-top:16px" id="races-detail-card" hidden>
        <h2 id="races-detail-title">赛道详情</h2>
        <div id="races-detail"></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>按赛事查</h2>
        <div class="toolbar">
          <input id="races-query" type="search" placeholder="赛事名关键词，例如 德比 / 纪念 / 锦标赛" />
          <label><input id="races-official" type="checkbox" checked /> 只看正式赛事</label>
          <button id="races-search">查询</button>
        </div>
        <div class="hint" id="races-list-hint"></div>
        <div class="list grid" id="races-list"></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>场地状态出现率</h2>
        <p class="hint" style="margin-top:0">取自 master.mdb 的 <code>race_condition</code>：同一区组与季节内 rate 之和恒为 1e8，所以 rate/1e8 就是出现率。</p>
        <div class="toolbar">
          <label>区组 <select id="cond-area"></select></label>
          <label>季节 <select id="cond-season"></select></label>
          <span class="hint" id="cond-hint"></span>
        </div>
        <div id="cond-table"></div>
        <p class="hint">天气与马场不是自由组合：雨只配重 / 不良，雪只配稍重 / 重且只出现在季节 4，晴与阴只配良 / 稍重（另有 rate 为 0 的占位行）。这是出现率，不是数值修正系数 —— 速度、加速度与耐力消耗的修正值在 master.mdb 与 BWiki 数据页里都没有，所以不推导。机制说明见仓库内 <code>docs/kb/04-races.md</code>。</p>
      </section>
    `;

    const venueEl = root.querySelector("#f-venue");
    const surfaceEl = root.querySelector("#f-surface");
    const directionEl = root.querySelector("#f-direction");
    const minEl = root.querySelector("#f-min");
    const maxEl = root.querySelector("#f-max");
    const geometryEl = root.querySelector("#f-geometry");
    const coursesEl = root.querySelector("#races-courses");
    const coursesCountEl = root.querySelector("#races-courses-count");
    const detailCard = root.querySelector("#races-detail-card");
    const detailTitle = root.querySelector("#races-detail-title");
    const detailEl = root.querySelector("#races-detail");
    const listEl = root.querySelector("#races-list");
    const listHintEl = root.querySelector("#races-list-hint");
    const queryEl = root.querySelector("#races-query");
    const officialEl = root.querySelector("#races-official");
    const sourceEl = root.querySelector("#races-source");
    const refreshHintEl = root.querySelector("#races-refresh-hint");
    const refreshResultEl = root.querySelector("#races-refresh-result");
    const refreshButton = root.querySelector("#races-refresh");
    const condAreaEl = root.querySelector("#cond-area");
    const condSeasonEl = root.querySelector("#cond-season");
    const condTableEl = root.querySelector("#cond-table");
    const condHintEl = root.querySelector("#cond-hint");

    function setRefreshResult(text) {
      refreshResultEl.textContent = text || "";
      refreshResultEl.hidden = !text;
    }

    function renderSource() {
      const counts = status.counts || {};
      const geometry = status.geometry || null;
      const catalog = status.catalog || null;
      refreshHintEl.textContent = status.mdb?.exists
        ? `master.mdb ${status.mdb.size_mb} MB`
        : "未找到 master.mdb，赛事目录无法导入";
      const geometryLine = geometry
        ? `${escapeHtml(geometry.page)} · revid ${num(geometry.revid)} · 抓取于 ${escapeHtml(
            shortTime(geometry.fetched_at),
          )}${geometry.from_cache ? "（缓存）" : ""}${geometry.stale ? "（抓取失败，用旧缓存）" : ""}`
        : "还没有导入赛道几何";
      sourceEl.innerHTML = `
        <dt>赛事</dt><dd>${counts.races || 0} 场（正式赛事 ${counts.official_races || 0}）</dd>
        <dt>赛道</dt><dd>${counts.courses || 0} 条，其中有几何数据 ${
          counts.courses_with_geometry || 0
        } 条</dd>
        <dt>场地状态</dt><dd>${num(counts.conditions)} 条出现率记录</dd>
        <dt>大赛排期</dt><dd>${counts.champions || 0} 届（群英联赛）· 在「日程」页查看</dd>
        <dt>目录来源</dt><dd>${escapeHtml(catalog?.source || "未导入")}${
          catalog ? ` · 导入于 ${escapeHtml(shortTime(catalog.imported_at))}` : ""
        }</dd>
        <dt>几何来源</dt><dd>C 级 · ${geometryLine}${
          geometry?.url
            ? ` · <a class="source-link" href="${escapeHtml(geometry.url)}" target="_blank" rel="noreferrer">原页</a>`
            : ""
        }</dd>
        <dt>数据版本</dt><dd>${escapeHtml(status.data_version?.version || "未知")}</dd>
      `;
      if (!counts.races) {
        setRefreshResult(
          status.mdb?.exists
            ? "还没有赛事目录，点「刷新赛道数据」导入一次。"
            : `没有找到 ${escapeHtml(status.mdb?.path || "master.mdb")}，先在「数据同步」里导入一次。`,
        );
      }
    }

    function courseRow(item) {
      const warnings = (item.warnings || []).length;
      return `
        <div class="list-row clickable" data-course="${escapeHtml(item.course_id)}" tabindex="0">
          <div>
            <div class="name">${escapeHtml(item.label)}</div>
            <div class="meta">${escapeHtml(item.course_id)} · 方向 ${escapeHtml(
              item.direction || "—",
            )} · ${escapeHtml(item.course_part || "未区分")} · 终直 ${item.final_straight}m · 直线 ${item.straight_count} 段${
              item.opening_straight ? `（含切线 ${item.opening_straight}m）` : ""
            } · 赛事 ${item.race_count} 场${
              warnings ? ` · ${warnings} 条提示` : ""
            }</div>
          </div>
          ${severity(item)}
        </div>`;
    }

    function renderCourses() {
      const items = courses?.items || [];
      const counts = courses?.counts || {};
      coursesCountEl.textContent = items.length
        ? `共 ${items.length} 条赛道 · 有几何 ${counts.with_geometry || 0} 条 · 引用赛事 ${counts.returns || 0} 次`
        : "没有符合条件的赛道。";
      coursesEl.innerHTML = items.length
        ? `${limiter.slice("courses", items).map(courseRow).join("")}${limiter.more(
            "courses",
            items.length,
          )}`
        : `<div class="empty">换个筛选条件，或者先点上面的「刷新赛道数据」。</div>`;
    }

    function renderDetail(item) {
      if (!item) {
        detailCard.hidden = true;
        return;
      }
      detailCard.hidden = false;
      detailTitle.textContent = `${item.label}（${item.course_id}）`;
      const warnings = (item.warnings || [])
        .map((text) => `<li>${escapeHtml(text)}</li>`)
        .join("");
      const racesList = (item.races || [])
        .map((name) => `<span class="tag">${escapeHtml(name)}</span>`)
        .join(" ");
      detailEl.innerHTML = `
        <dl class="kv">
          <dt>赛场 / 场地 / 距离</dt><dd>${escapeHtml(item.venue)} · ${escapeHtml(
            item.surface,
          )} · ${item.distance}m</dd>
          <dt>方向 / 内外</dt><dd>${escapeHtml(item.direction || "—")} · ${escapeHtml(
            item.course_part || "未区分",
          )}</dd>
          <dt>终直 / 直线段</dt><dd>${
            item.final_straight ? `${item.final_straight}m` : "无"
          } / ${item.straight_count} 段（列出合计 ${item.straight_total}m${
            item.opening_straight ? `，含切线 ${item.opening_straight}m` : ""
          }${item.unlisted_length ? `，另有 ${item.unlisted_length}m 未列出` : ""}）</dd>
          <dt>坡道</dt><dd>标号 +1 共 ${item.slope_positive}m · 标号 -1 共 ${
            item.slope_negative
          }m</dd>
          <dt>分段总长</dt><dd>${item.total_length}m${
            item.has_geometry ? "" : "（无几何数据）"
          }</dd>
          <dt>赛事</dt><dd>${racesList || "没有赛事引用这条赛道"}</dd>
        </dl>
        ${warnings ? `<ul class="hint" style="margin:10px 0 0">${warnings}</ul>` : ""}
        <h3 style="margin-top:16px">分段</h3>
        ${segmentRows(item.segments || [])}
        <h3 style="margin-top:16px">坡道</h3>
        ${slopeRows(item.slopes || [])}
        <p class="hint">坡道的「标号」是数据页的原始值（只有 +1 / -1），页面没有说明哪一侧是上坡，这里不推断。</p>
        <p class="hint">「切线」是数据页没有列出、由首段起点推出的开场直线（标 <span class="pill muted">推断</span>）；中段空档仍单列为「未列出」，不并进直线段。</p>
      `;
    }

    function raceRow(item) {
      return `
        <div class="list-row clickable" data-race="${item.race_id}" tabindex="0">
          <div>
            <div class="name">${escapeHtml(item.name)}</div>
            <div class="meta">${escapeHtml(item.venue || "—")} · ${escapeHtml(
              item.surface || "—",
            )} · ${num(item.distance)}m · 方向 ${escapeHtml(item.direction || "—")} · ${escapeHtml(
              item.grade_label || "级别未知",
            )} · ${escapeHtml(item.course_id || "无赛道")}</div>
          </div>
          <span class="pill info">${escapeHtml(item.grade_label || "—")}</span>
        </div>`;
    }

    function renderRaces() {
      const items = races?.items || [];
      listHintEl.textContent = items.length
        ? `显示 ${items.length} 场${items.length >= races.limit ? `（上限 ${races.limit}，用关键词缩小范围）` : ""}`
        : "没有匹配的赛事。";
      listEl.innerHTML = items.length
        ? `${limiter.slice("races", items).map(raceRow).join("")}${limiter.more("races", items.length)}`
        : "";
    }
    function percentText(item) {
      if (!item.rate) return "0%";
      // 极低概率四舍五入会变成 0.00%，单独标出来，避免误读成「不出现」
      if (item.percent < 0.01) return "＜0.01%";
      return `${item.percent}%`;
    }

    function renderConditions() {
      const groups = conditions?.groups || [];
      if (!groups.length) {
        condAreaEl.innerHTML = "";
        condSeasonEl.innerHTML = "";
        condHintEl.textContent = "还没有场地状态数据。";
        condTableEl.innerHTML = "";
        return;
      }
      const areas = [...new Set(groups.map((group) => group.area))];
      const areaValue = conditions.selected?.area ?? "";
      condAreaEl.innerHTML = areas
        .map((area) => {
          const label = groups.find((group) => group.area === area)?.area_label || `区域 ${area}`;
          return `<option value="${area}"${area === areaValue ? " selected" : ""}>${escapeHtml(
            label,
          )}</option>`;
        })
        .join("");
      const seasons = groups.filter((group) => group.area === areaValue).map((group) => group.season);
      const seasonValue = conditions.selected?.season ?? "";
      condSeasonEl.innerHTML = seasons
        .map((season) => `<option value="${season}"${season === seasonValue ? " selected" : ""}>季节 ${season}</option>`)
        .join("");

      const total = conditions.selected?.total || 0;
      condHintEl.textContent =
        total === 100000000
          ? `${conditions.items.length} 种组合 · rate 合计 ${total}（= 100%）`
          : `${conditions.items.length} 种组合 · rate 合计 ${total}，与 1e8 不一致，源数据可能有缺失`;
      const rows = conditions.items
        .map(
          (item) => `<tr>
            <td>${escapeHtml(item.weather_label || "—")}</td>
            <td>${escapeHtml(item.ground_label || "—")}</td>
            <td class="mono">${percentText(item)}</td>
            <td class="mono">${item.rate}</td>
          </tr>`,
        )
        .join("");
      condTableEl.innerHTML = `<table class="seg-table">
        <thead><tr><th>天气</th><th>马场</th><th>出现率</th><th>rate 原值</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }

    async function loadConditions(area, season) {
      const params = new URLSearchParams();
      if (area !== undefined && area !== null) params.set("area", area);
      if (season !== undefined && season !== null) params.set("season", season);
      conditions = await api(`${API}/conditions?${params.toString()}`);
      renderConditions();
    }

    function filters() {
      const params = new URLSearchParams();
      if (venueEl.value) params.set("venue", venueEl.value);
      if (surfaceEl.value) params.set("surface", surfaceEl.value);
      if (directionEl.value) params.set("direction", directionEl.value);
      if (minEl.value) params.set("distance_min", minEl.value);
      if (maxEl.value) params.set("distance_max", maxEl.value);
      if (geometryEl.checked) params.set("geometry_only", "true");
      return params;
    }

    async function loadCourses() {
      courses = await api(`${API}/courses?${filters().toString()}`);
      renderCourses();
    }

    async function loadRaces() {
      const params = new URLSearchParams();
      if (queryEl.value.trim()) params.set("q", queryEl.value.trim());
      if (officialEl.checked) params.set("official_only", "true");
      if (venueEl.value) params.set("venue", venueEl.value);
      if (surfaceEl.value) params.set("surface", surfaceEl.value);
      races = await api(`${API}/races?${params.toString()}`);
      renderRaces();
    }

    async function openCourse(courseId) {
      try {
        const item = await api(`${API}/courses/${encodeURIComponent(courseId)}`);
        selected = item;
        renderDetail(item);
        detailCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (error) {
        toast(error.message, "error");
      }
    }

    function fillOptions() {
      venueEl.insertAdjacentHTML(
        "beforeend",
        (status.venues || [])
          .map((item) => `<option value="${escapeHtml(item.venue)}">${escapeHtml(item.venue)}</option>`)
          .join(""),
      );
      surfaceEl.insertAdjacentHTML(
        "beforeend",
        (status.surfaces || [])
          .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
          .join(""),
      );
      directionEl.insertAdjacentHTML(
        "beforeend",
        (status.directions || [])
          .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
          .join(""),
      );
    }

    // 长列表的「展开其余 N 条」
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-more]");
      if (!button) return;
      const key = button.dataset.more;
      limiter.toggle(key);
      if (key === "courses") renderCourses();
      else if (key === "races") renderRaces();
    });

    coursesEl.addEventListener("click", (event) => {
      const row = event.target.closest("[data-course]");
      if (row) openCourse(row.dataset.course);
    });
    coursesEl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("[data-course]");
      if (!row) return;
      event.preventDefault();
      openCourse(row.dataset.course);
    });
    listEl.addEventListener("click", (event) => {
      const row = event.target.closest("[data-race]");
      if (!row) return;
      openCourse(row.dataset.race ? raceCourseId(row.dataset.race) : null);
    });

    function raceCourseId(raceId) {
      const item = (races?.items || []).find((race) => String(race.race_id) === String(raceId));
      return item?.course_id;
    }

    condAreaEl.addEventListener("change", () => {
      loadConditions(Number(condAreaEl.value)).catch((error) => toast(error.message, "error"));
    });
    condSeasonEl.addEventListener("change", () => {
      loadConditions(Number(condAreaEl.value), Number(condSeasonEl.value)).catch((error) =>
        toast(error.message, "error"),
      );
    });

    for (const element of [venueEl, surfaceEl, directionEl, minEl, maxEl, geometryEl]) {
      element.addEventListener("change", () => {
        loadCourses().catch((error) => toast(error.message, "error"));
      });
    }
    root.querySelector("#f-clear").addEventListener("click", () => {
      venueEl.value = "";
      surfaceEl.value = "";
      directionEl.value = "";
      minEl.value = "";
      maxEl.value = "";
      geometryEl.checked = false;
      loadCourses().catch((error) => toast(error.message, "error"));
    });
    root.querySelector("#races-search").addEventListener("click", () => {
      loadRaces().catch((error) => toast(error.message, "error"));
    });
    queryEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        loadRaces().catch((error) => toast(error.message, "error"));
      }
    });

    refreshButton.addEventListener("click", async () => {
      if (refreshing) return;
      refreshing = true;
      refreshButton.disabled = true;
      refreshButton.textContent = "刷新中…";
      setRefreshResult("正在导入赛事目录并抓取赛道数据页，请稍候。");
      try {
        const result = await api(`${API}/refresh`, {
          method: "POST",
          body: JSON.stringify({ force: true }),
        });
        status = await api(`${API}/status`);
        renderSource();
        await loadCourses();
        await loadRaces();
        await loadConditions();
        const lines = [];
        if (result.catalog) {
          lines.push(
            `赛事目录：${result.catalog.races} 场 / 赛道 ${result.catalog.courses} 条（跳过无名称 ${result.catalog.skipped} 场）· 大赛 ${result.catalog.champions} 届 · 场地状态 ${result.catalog.conditions} 条`,
          );
        }
        if (result.geometry) {
          lines.push(
            `赛道几何：${result.geometry.courses} 条${
              result.geometry.with_warnings
                ? `，其中 ${result.geometry.with_warnings} 条有提示`
                : ""
            }${result.geometry.missing_geometry ? `；${result.geometry.missing_geometry} 条没有几何数据` : ""}`,
          );
        }
        if (result.error) lines.push(`问题：${result.error}`);
        (result.skipped || []).slice(0, 5).forEach((line) => lines.push(`· ${line}`));
        setRefreshResult(lines.join("\n") || "刷新完成。");
        toast(`刷新完成：赛道 ${result.geometry?.courses ?? 0} 条`, "info");
      } catch (error) {
        setRefreshResult(`刷新失败：${error.message}`);
        toast(error.message, "error");
      } finally {
        refreshing = false;
        refreshButton.disabled = false;
        refreshButton.textContent = "刷新赛道数据";
      }
    });

    try {
      status = await api(`${API}/status`);
      renderSource();
      fillOptions();
      await Promise.all([loadCourses(), loadRaces(), loadConditions()]);
    } catch (error) {
      setRefreshResult(`加载失败：${error.message}`);
    }
  },
};
