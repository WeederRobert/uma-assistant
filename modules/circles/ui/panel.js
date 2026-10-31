const API = "/api/circles";

// 接口给的是带时区偏移的 ISO 字符串，必须按系统时区换算后再截断
function shortTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  const pad2 = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  );
}

// fetch 失败时浏览器只给 "Failed to fetch"，对用户没有信息量
function describeError(error) {
  const message = error?.message ? String(error.message) : String(error);
  if (/Failed to fetch|NetworkError|Load failed|ERR_CONNECTION/i.test(message)) {
    return "连不上本地服务（可能已停止或在别的端口），先确认助手还在运行";
  }
  return message;
}

function shareCell(percent, text) {
  if (percent === null || percent === undefined) return `<span class="hint">—</span>`;
  const width = Math.max(0, Math.min(100, percent));
  return `
    <div style="display:flex;align-items:center;gap:8px">
      <div style="flex:1;min-width:60px;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
        <div style="width:${width}%;height:100%;background:var(--accent)"></div>
      </div>
      <span class="mono">${text}</span>
    </div>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast } = ctx;
    let refreshing = false;

    root.innerHTML = `
      <h1 class="page-title">社团 <span class="pill warn" style="vertical-align:middle">实验性</span></h1>
      <p class="page-desc">读取 <span class="mono">umamusume.space</span> 的公开接口，看<strong>社团当月增粉</strong>与<strong>成员贡献占比</strong>。数据由第三方玩家自采，不是官方数据；站点只收录约 160 个社团，没收录的社团查不到成员明细。所有时间按系统时区显示。</p>

      <section class="card">
        <h2>数据来源</h2>
        <div class="toolbar">
          <button class="ghost" id="circles-refresh">拉取社团月榜</button>
          <span class="hint" id="circles-refresh-hint"></span>
        </div>
        <dl class="kv" id="circles-source"></dl>
        <div class="answer" id="circles-refresh-result" style="margin-top:12px" hidden></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>社团月增粉丝</h2>
        <div class="toolbar">
          <input id="circles-query" type="search" placeholder="社团名或社团 ID，例如 颂乐人偶 / 264444677" style="min-width:260px" />
          <label>排序
            <select id="circles-sort">
              <option value="month">当月增粉</option>
              <option value="today">当日新增</option>
              <option value="rank">月榜排名</option>
            </select>
          </label>
          <button class="primary" id="circles-search">查询</button>
          <span class="hint" id="circles-list-hint"></span>
        </div>
        <div id="circles-list"></div>
      </section>

      <section class="card" style="margin-top:16px" id="circles-detail-card" hidden>
        <h2 id="circles-detail-title">成员贡献</h2>
        <div id="circles-detail-body"></div>
      </section>
    `;

    const refreshBtn = root.querySelector("#circles-refresh");
    const refreshHint = root.querySelector("#circles-refresh-hint");
    const refreshResult = root.querySelector("#circles-refresh-result");
    const sourceEl = root.querySelector("#circles-source");
    const queryEl = root.querySelector("#circles-query");
    const sortEl = root.querySelector("#circles-sort");
    const searchBtn = root.querySelector("#circles-search");
    const listEl = root.querySelector("#circles-list");
    const listHint = root.querySelector("#circles-list-hint");
    const detailCard = root.querySelector("#circles-detail-card");
    const detailTitle = root.querySelector("#circles-detail-title");
    const detailBody = root.querySelector("#circles-detail-body");

    function setResult(text) {
      refreshResult.textContent = text || "";
      refreshResult.hidden = !text;
    }

    function renderNotes(notes) {
      if (!notes || !notes.length) return "";
      return `<ul class="hint" style="margin:6px 0 0;padding-left:18px">${notes
        .map((note) => `<li>${escapeHtml(note)}</li>`)
        .join("")}</ul>`;
    }

    function renderSource(status) {
      const rows = [
        ["接口来源", status.source],
        ["上次拉取", shortTime(status.fetched_at)],
        ["当月基线", shortTime(status.month_baseline_at)],
        [
          "站点采集间隔",
          status.capture_interval_minutes ? `${status.capture_interval_minutes} 分钟` : "未知",
        ],
        ["已存社团 / 成员", `${status.circle_count} / ${status.member_rows}`],
        ["成员明细缓存", `${status.member_cache_minutes} 分钟`],
      ];
      sourceEl.innerHTML = rows
        .map(
          ([key, value]) =>
            `<dt>${escapeHtml(key)}</dt><dd class="mono">${escapeHtml(value)}</dd>`,
        )
        .join("");
      refreshHint.textContent = status.circle_count
        ? `已存 ${status.circle_count} 个社团`
        : "本机还没有数据";
    }

    async function loadStatus() {
      try {
        const status = await api(`${API}/status`);
        renderSource(status);
        return status;
      } catch (error) {
        sourceEl.innerHTML = `<dt>状态</dt><dd>${escapeHtml(describeError(error))}</dd>`;
        return null;
      }
    }

    function renderList(data) {
      if (!data.items.length) {
        listEl.innerHTML = `<p class="empty">没有匹配的社团。 ${
          data.fetched_at ? "换个关键词试试。" : "点上面的「拉取社团月榜」获取数据。"
        }</p>`;
        listHint.textContent = "";
        return;
      }
      listHint.textContent = `显示 ${data.count} 个社团 · 数据时间 ${shortTime(data.fetched_at)}`;
      listEl.innerHTML = `
        <table class="seg-table">
          <thead>
            <tr>
              <th>月榜</th><th>社团</th><th>成员</th>
              <th>当月增粉</th><th>当日新增</th><th></th>
            </tr>
          </thead>
          <tbody>
            ${data.items
              .map(
                (item) => `
              <tr>
                <td class="mono">${item.ranking_rank ? `#${item.ranking_rank}` : "—"}</td>
                <td><strong>${escapeHtml(item.name)}</strong>
                  <div class="hint mono">${item.circle_id}</div></td>
                <td class="mono">${item.member_count ?? "—"}</td>
                <td class="mono">${escapeHtml(item.month_fans_text)}</td>
                <td class="mono">${escapeHtml(item.today_fans_text)}</td>
                <td><button class="ghost" data-circle="${item.circle_id}">${
                  item.has_members ? "查看成员" : "拉取成员"
                }</button></td>
              </tr>`,
              )
              .join("")}
          </tbody>
        </table>
        ${renderNotes(data.notes)}`;
      listEl.querySelectorAll("button[data-circle]").forEach((button) => {
        button.addEventListener("click", () => openCircle(Number(button.dataset.circle)));
      });
    }

    async function loadList() {
      const params = new URLSearchParams({ sort: sortEl.value });
      const query = queryEl.value.trim();
      if (query) params.set("q", query);
      listHint.textContent = "加载中…";
      try {
        const data = await api(`${API}?${params.toString()}`);
        renderList(data);
        return data;
      } catch (error) {
        listEl.innerHTML = `<div class="answer">${escapeHtml(describeError(error))}</div>`;
        listHint.textContent = "";
        return null;
      }
    }

    function renderDetail(data) {
      const { circle, summary, members } = data;
      detailCard.hidden = false;
      detailTitle.innerHTML = `${escapeHtml(circle.name)}
        <span class="pill">#${circle.ranking_rank ?? "—"}</span>
        ${data.stale ? '<span class="pill warn">缓存</span>' : ""}`;

      const summaryRows = [
        ["当月增粉（榜单）", circle.month_fans_text],
        ["成员合计", summary.member_month_fans_text],
        ["差值（榜单 − 成员）", `${summary.gap_fans_text}${
          summary.gap_percent_text ? `（${summary.gap_percent_text}）` : ""
        }`],
        ["成员 / 有贡献", `${summary.member_count} / ${summary.contributor_count}`],
        ["人均当月增粉", summary.per_member_fans_text],
        ["成员明细时间", shortTime(data.fetched_at)],
      ];

      detailBody.innerHTML = `
        <div class="toolbar" style="margin-bottom:8px">
          <button class="ghost" id="circles-detail-refresh">重新拉取成员明细</button>
          <span class="hint">总粉丝 ${escapeHtml(circle.total_fans_text)} · 当日新增 ${escapeHtml(
            circle.today_fans_text,
          )} · 当月基线 ${escapeHtml(shortTime(circle.month_baseline_at))}</span>
        </div>
        <dl class="kv">${summaryRows
          .map(
            ([key, value]) =>
              `<dt>${escapeHtml(key)}</dt><dd class="mono">${escapeHtml(value)}</dd>`,
          )
          .join("")}</dl>
        <table class="seg-table" style="margin-top:12px">
          <thead>
            <tr>
              <th>成员</th><th>当月增粉</th><th style="min-width:160px">贡献占比</th>
              <th>日均</th><th>本月育成</th><th>最后登录</th>
            </tr>
          </thead>
          <tbody>
            ${members
              .map(
                (member) => `
              <tr>
                <td>${escapeHtml(member.member_name)}</td>
                <td class="mono">${escapeHtml(member.month_fan_text)}</td>
                <td>${shareCell(member.share_percent, escapeHtml(member.share_text))}</td>
                <td class="mono">${escapeHtml(member.daily_avg_text)}</td>
                <td class="mono">${member.month_training_count ?? "—"}</td>
                <td class="mono">${escapeHtml(shortTime(member.last_login_time))}</td>
              </tr>`,
              )
              .join("")}
          </tbody>
        </table>
        ${renderNotes(data.notes)}`;

      detailBody
        .querySelector("#circles-detail-refresh")
        .addEventListener("click", () => openCircle(circle.circle_id, true));
    }

    async function openCircle(circleId, force = false) {
      detailCard.hidden = false;
      detailTitle.textContent = "成员贡献";
      detailBody.innerHTML = `<p class="hint">正在拉取成员明细（约 1.4 MB，首次约 2 秒）…</p>`;
      try {
        const data = await api(`${API}/${circleId}${force ? "?refresh=true" : ""}`);
        renderDetail(data);
      } catch (error) {
        const message = describeError(error);
        detailBody.innerHTML = `<div class="answer">${escapeHtml(message)}</div>`;
        toast(message, "error");
      }
    }

    async function doRefresh(auto = false) {
      if (refreshing) return;
      refreshing = true;
      refreshBtn.disabled = true;
      refreshHint.textContent = auto ? "首次进入，正在拉取社团月榜…" : "正在拉取社团月榜…";
      try {
        const result = await api(`${API}/refresh`, { method: "POST" });
        setResult(
          `已拉取 ${result.fetched} 个社团（${result.duration_ms} ms），` +
            `数据时间 ${shortTime(result.fetched_at)}。`,
        );
        await loadStatus();
        await loadList();
      } catch (error) {
        const message = describeError(error);
        setResult(`拉取失败：${message}`);
        toast(message, "error");
      } finally {
        refreshing = false;
        refreshBtn.disabled = false;
      }
    }

    refreshBtn.addEventListener("click", () => doRefresh(false));
    searchBtn.addEventListener("click", loadList);
    queryEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadList();
    });
    sortEl.addEventListener("change", loadList);

    const status = await loadStatus();
    if (status && status.circle_count === 0) await doRefresh(true);
    else await loadList();

    return () => {
      refreshing = false;
    };
  },
};
