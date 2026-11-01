// 千里眼 + 萝卜规划面板：所有口径（天数、成本、偏差校正、够不够）都由后端算好，
// 这里只做展示与表单。跨模块的数据（大赛）不在这里取；本模块自己读 master.mdb
// 的已确认排期，避免依赖别的模块是否加载成功。
const API = "/api/planner";

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
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function carats(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString("zh-CN");
}

function stateLabel(item) {
  if (item.state === "active") {
    if (item.days_left === null || item.days_left === undefined) return "进行中";
    if (item.days_left <= 0) return "今天结束";
    return `还剩 ${item.days_left} 天`;
  }
  if (item.state === "upcoming") {
    const days = item.days_to_start;
    if (days === 0) return "今天开始";
    if (days === 1) return "明天开始";
    if (days > 1) return `${days} 天后开始`;
    return "即将开始";
  }
  return "已结束";
}

function stateTone(item) {
  if (item.state === "active") return "ok";
  if (item.state === "upcoming") return "info";
  return "muted";
}

function levelTag(item, escapeHtml) {
  const level = String(item.source_level || "?").toUpperCase();
  const tone = level === "A" ? "a" : level === "B" ? "b" : level === "C" ? "c" : "d";
  const label =
    level === "A" ? "master.mdb（简中服实际排期）" : level === "C" ? "BWiki 社区推算" : `${level} 级`;
  return `<span class="tag level-${tone}" title="${escapeHtml(label)}">${escapeHtml(level)} 级</span>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, createLimiter } = ctx;
    const limiter = createLimiter(8);
    const selected = new Map();
    let forecast = null;
    let status = null;
    let evaluation = null;
    let plans = [];
    let planId = null;
    let days = 180;
    let showOverlap = false;
    let busy = false;

    root.innerHTML = `
      <h1 class="page-title">规划 · 千里眼</h1>
      <p class="page-desc">
        近半年的<strong>卡池与剧情活动</strong>：已确认段来自本机
        <span class="mono">master.mdb</span>（简中服客户端的实际排期，A 级），
        预测段来自 BWiki 的「预测卡池」（C 级，<strong>已按 mdb 实测偏差校正</strong>）。
        简中服正在加速，日服时间对不上轴，所以活动只用 mdb 的排期、不做跨服换算。
        勾选想要的池子就能算萝卜够不够。所有时间按系统时区显示。
      </p>
      <section class="card">
        <h2>千里眼：近半年预测</h2>
        <div class="toolbar">
          <label>窗口
            <select id="planner-days">
              <option value="60">60 天</option>
              <option value="90">90 天</option>
              <option value="180" selected>半年（180 天）</option>
              <option value="365">一年</option>
            </select>
          </label>
          <label><input id="planner-overlap" type="checkbox" /> 显示与 mdb 重叠的预测行</label>
          <button class="ghost" id="planner-refresh">刷新预测</button>
          <span class="hint" id="planner-hint">加载中…</span>
        </div>
        <div id="planner-forecast"></div>
      </section>
      <section class="card" style="margin-top:16px">
        <h2>钻石（萝卜）规划</h2>
        <p class="hint" style="margin-top:0">
          收入只能你自己填：库里没有任何萝卜收入数据，所以这里不估算，只按你填的月收入线性折算到每个目标池开始的那天
          （30 天/月）。券按 1 张 = 1 抽抵扣，优先用券。
        </p>
        <div class="toolbar">
          <label>当前萝卜 <input id="plan-carats" type="number" min="0" step="100" value="0" style="width:110px" /></label>
          <label>抽券 <input id="plan-tickets" type="number" min="0" step="1" value="0" style="width:80px" /></label>
          <label>每月收入 <input id="plan-income" type="number" min="0" step="100" value="0" style="width:110px" /></label>
          <button class="primary" id="plan-evaluate">试算</button>
          <button class="ghost" id="plan-save">保存方案</button>
          <button class="ghost" id="plan-reset">重置</button>
          <span class="hint" id="plan-hint"></span>
        </div>
        <div id="plan-targets"></div>
        <div id="plan-result" style="margin-top:12px"></div>
      </section>
      <section class="card" style="margin-top:16px">
        <h2>已存方案</h2>
        <div id="plan-saved"></div>
      </section>
    `;

    const hintEl = root.querySelector("#planner-hint");
    const forecastEl = root.querySelector("#planner-forecast");
    const targetsEl = root.querySelector("#plan-targets");
    const resultEl = root.querySelector("#plan-result");
    const planHintEl = root.querySelector("#plan-hint");
    const savedEl = root.querySelector("#plan-saved");
    const caratsEl = root.querySelector("#plan-carats");
    const ticketsEl = root.querySelector("#plan-tickets");
    const incomeEl = root.querySelector("#plan-income");

    function poolsByUid() {
      const map = new Map();
      for (const pool of forecast?.pools || []) map.set(pool.uid, pool);
      return map;
    }

    // ---------------------------------------------------------------- forecast
    function poolRow(item) {
      const checked = selected.has(item.uid) ? "checked" : "";
      const pickups = (item.pickups || [])
        .slice(0, 4)
        .map((name) => `<span class="tag">${escapeHtml(name)}</span>`)
        .join("");
      const correction = item.correction_days
        ? `<span class="tag level-c">wiki 原值 ${dateOnly(
            item.source_starts_local,
          )}，按实测偏差前移 ${item.correction_days} 天</span>`
        : "";
      return `
        <div class="list-row">
          <div>
            <div class="name">
              <label><input type="checkbox" data-pool="${escapeHtml(item.uid)}" ${checked} /> ${escapeHtml(
                item.title,
              )}</label>
            </div>
            <div class="meta">
              <span class="pill ${stateTone(item)}">${escapeHtml(stateLabel(item))}</span>
              <span>${shortTime(item.starts_local)} → ${shortTime(item.ends_local)}</span>
              <span class="tag">${escapeHtml(item.segment_label)}</span>
              ${item.card_type_label && item.card_type_label !== "—" ? `<span class="tag">${escapeHtml(item.card_type_label)}</span>` : ""}
              ${levelTag(item, escapeHtml)}
              ${pickups}
            </div>
            <div class="meta">
              <span class="mono">单抽 ${carats(item.cost_single)} · 天井 ${item.ceiling_pulls} 抽 = ${carats(
                item.ceiling_carats,
              )} 萝卜</span>
              ${correction}
              ${
                item.source_url
                  ? ` · <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">来源页面</a>`
                  : ""
              }
            </div>
          </div>
        </div>`;
    }

    function eventRow(item) {
      return `
        <div class="list-row">
          <div>
            <div class="name">${escapeHtml(item.title)}</div>
            <div class="meta">
              <span class="pill ${stateTone(item)}">${escapeHtml(stateLabel(item))}</span>
              ${item.kind ? `<span class="tag">${escapeHtml(item.kind)}</span>` : ""}
              ${levelTag(item, escapeHtml)}
            </div>
            <div class="meta">
              <span>${shortTime(item.starts_local)} → ${shortTime(item.ends_local)}</span>
            </div>
          </div>
        </div>`;
    }

    function calibrationText() {
      const calibration = forecast?.calibration || {};
      if (!calibration.pairs) return "预测段未校正（没有可比对的样本）";
      const range =
        calibration.min_days === calibration.max_days
          ? `+${calibration.min_days} 天`
          : `+${calibration.min_days}~+${calibration.max_days} 天`;
      return `wiki 偏晚实测 ${range}（${calibration.pairs} 组配对，按最近一次 ${calibration.used_days} 天前移；偏差仍在扩大）`;
    }

    function section(key, title, items, rowFn, emptyText) {
      if (!items.length) return `<h3 style="margin-top:14px">${title}（0）</h3><div class="empty">${emptyText}</div>`;
      return `
        <h3 style="margin-top:14px">${title}（${items.length}）</h3>
        <div class="list grid">${limiter.slice(key, items).map(rowFn).join("")}</div>
        ${limiter.more(key, items.length)}`;
    }

    function renderForecast() {
      if (!forecast) {
        forecastEl.innerHTML = `<div class="empty">还没有加载预测。</div>`;
        return;
      }
      const confirmed = forecast.pools.filter((item) => item.segment === "confirmed");
      const predicted = forecast.pools.filter((item) => item.segment === "predicted");
      const overlap = forecast.pools.filter((item) => item.segment === "overlap");
      hintEl.textContent = `窗口 ${forecast.window.days} 天 · 已确认到 ${dateOnly(
        forecast.horizon.confirmed_until_local,
      )} · ${calibrationText()}`;
      forecastEl.innerHTML = `
        ${section(
          "pool:confirmed",
          "已确认卡池（master.mdb）",
          confirmed,
          poolRow,
          "本机 master.mdb 里没有进行中或未来的卡池。先在「数据同步」导入一次客户端数据。",
        )}
        ${section(
          "pool:predicted",
          "预测卡池（BWiki，已按实测偏差校正）",
          predicted,
          poolRow,
          "BWiki 没有给出超出已确认段的预测行；点「刷新预测」重新抓一次。",
        )}
        ${section(
          "event",
          "剧情活动（master.mdb 排期）",
          forecast.events,
          eventRow,
          "窗口内没有剧情活动排期。育成活动（竞速嘉年华等）没有可核对的简中排期，不显示。",
        )}
        ${
          overlap.length
            ? `<details style="margin-top:14px">
                 <summary class="hint">与 mdb 已确认段重叠的预测行（${overlap.length}）：只做对照，不参与规划</summary>
                 <div class="list grid" style="margin-top:8px">${overlap
                   .slice(0, 12)
                   .map(poolRow)
                   .join("")}</div>
               </details>`
            : ""
        }
        <details style="margin-top:10px">
          <summary class="hint">口径与限制</summary>
          <ul class="hint" style="margin-top:8px">
            ${forecast.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}
            ${
              forecast.calibration?.note
                ? `<li>${escapeHtml(forecast.calibration.note)}</li>`
                : ""
            }
          </ul>
        </details>`;
    }

    // ----------------------------------------------------------------- targets
    function renderTargets() {
      const pools = poolsByUid();
      if (!selected.size) {
        targetsEl.innerHTML = `<div class="empty">在上面勾选想抽的卡池，这里会列出目标与计划抽数（默认按天井）。</div>`;
        return;
      }
      const rows = [...selected.entries()]
        .map(([uid, item]) => ({ pool: pools.get(uid), item }))
        .filter((row) => row.pool)
        .sort((a, b) => a.pool.start_ts - b.pool.start_ts);
      targetsEl.innerHTML = `
        <div class="list" style="margin-top:12px">
          ${rows
            .map(
              ({ pool, item }) => `
            <div class="list-row">
              <div>
                <div class="name">${escapeHtml(pool.title)}</div>
                <div class="meta">${shortTime(pool.starts_local)} · ${escapeHtml(
                  pool.segment_label,
                )} · 天井 ${pool.ceiling_pulls} 抽</div>
              </div>
              <div class="toolbar" style="margin:0">
                <label>抽数 <input type="number" min="0" max="1000" step="10" data-pulls="${escapeHtml(
                  pool.uid,
                )}" value="${item.pulls || 0}" style="width:90px" placeholder="${pool.ceiling_pulls}" /></label>
                <button class="ghost" data-remove="${escapeHtml(pool.uid)}">移除</button>
              </div>
            </div>`,
            )
            .join("")}
        </div>`;
    }

    function renderResult() {
      if (!evaluation) {
        resultEl.innerHTML = "";
        return;
      }
      const totals = evaluation.totals;
      const rows = evaluation.rows || [];
      resultEl.innerHTML = `
        <div class="answer">
          目标 <strong>${totals.targets}</strong> 个，买得起 <strong>${totals.affordable}</strong> 个；
          预计花掉 <strong>${carats(totals.spent_carats)}</strong> 萝卜，
          期间收入 <strong>${carats(totals.income_carats)}</strong>，
          期末结余 <strong>${carats(totals.ending_carats)}</strong> 萝卜 / ${totals.ending_tickets} 张券${
            totals.shortfall_carats > 0
              ? `；缺口合计 <strong>${carats(totals.shortfall_carats)}</strong> 萝卜`
              : ""
          }。
        </div>
        ${
          rows.length
            ? `<div class="list grid" style="margin-top:10px">${rows
                .map(
                  (row) => `
              <div class="list-row">
                <div>
                  <div class="name">${escapeHtml(row.title)}</div>
                  <div class="meta">
                    <span class="pill ${row.affordable ? "ok" : "warn"}">${
                      row.affordable ? "够" : `缺 ${carats(row.shortfall_carats)}`
                    }</span>
                    <span>${shortTime(row.starts_local)}</span>
                    <span>${row.pulls} 抽${row.tickets_used ? `（券抵 ${row.tickets_used}）` : ""}</span>
                  </div>
                  <div class="meta">
                    <span>需要 ${carats(row.cost_carats)} 萝卜</span>
                    <span>届时可用 ${carats(row.available_carats)}</span>
                    <span>结余 ${carats(row.balance_after)}</span>
                  </div>
                </div>
              </div>`,
                )
                .join("")}</div>`
            : ""
        }
        <ul class="hint" style="margin-top:10px">
          ${(evaluation.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("")}
        </ul>`;
    }

    function renderSaved() {
      if (!plans.length) {
        savedEl.innerHTML = `<div class="empty">还没有保存的方案。填好萝卜与目标后点「保存方案」。</div>`;
        return;
      }
      savedEl.innerHTML = `
        <div class="list">
          ${plans
            .map(
              (plan) => `
            <div class="list-row">
              <div>
                <div class="name">${escapeHtml(plan.name)}${
                  plan.id === planId ? ' <span class="pill info">当前</span>' : ""
                }</div>
                <div class="meta">
                  <span>萝卜 ${carats(plan.carats)}</span>
                  <span>券 ${plan.tickets}</span>
                  <span>月收入 ${carats(plan.monthly_income)}</span>
                  <span>${plan.targets} 个目标</span>
                  <span>${shortTime(plan.updated_at)}</span>
                </div>
              </div>
              <div class="toolbar" style="margin:0">
                <button class="ghost" data-load="${plan.id}">加载</button>
                <button class="ghost" data-delete="${plan.id}">删除</button>
              </div>
            </div>`,
            )
            .join("")}
        </div>`;
    }

    // ------------------------------------------------------------------ calls
    function payload() {
      const targets = [...selected.entries()]
        .map(([uid, item]) => ({ pool_uid: uid, pulls: Number(item.pulls) || 0, priority: 1 }))
        .filter((target) => poolsByUid().has(target.pool_uid));
      return {
        name: planId ? (plans.find((plan) => plan.id === planId)?.name ?? "我的规划") : "我的规划",
        carats: Number(caratsEl.value) || 0,
        tickets: Number(ticketsEl.value) || 0,
        monthly_income: Number(incomeEl.value) || 0,
        note: "",
        targets,
      };
    }

    async function loadForecast(withSpinner = true) {
      if (withSpinner) forecastEl.innerHTML = `<div class="empty">加载中…</div>`;
      forecast = await api(
        `${API}/forecast?days=${days}&include_overlap=${showOverlap ? "true" : "false"}`,
      );
      for (const uid of [...selected.keys()]) {
        if (!poolsByUid().has(uid)) selected.delete(uid);
      }
      renderForecast();
      renderTargets();
    }

    async function loadStatus() {
      status = await api(`${API}/status`);
      const counts = status.counts;
      if (!planHintEl.textContent) {
        planHintEl.textContent = `已确认 ${counts.confirmed} 池 / ${counts.events} 活动 · 预测 ${counts.predicted}`;
      }
    }

    async function loadPlans() {
      const data = await api(`${API}/plans`);
      plans = data.plans || [];
      renderSaved();
    }

    async function runEvaluate() {
      if (busy) return;
      busy = true;
      try {
        evaluation = await api(`${API}/evaluate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload()),
        });
        renderResult();
        if (!evaluation.rows.length) toast("还没有目标池：先在上面勾选");
      } catch (error) {
        toast(`试算失败：${error.message}`);
      } finally {
        busy = false;
      }
    }

    // ----------------------------------------------------------------- events
    root.querySelector("#planner-days").addEventListener("change", async (event) => {
      days = Number(event.target.value) || 180;
      try {
        await loadForecast();
      } catch (error) {
        toast(`加载失败：${error.message}`);
      }
    });

    root.querySelector("#planner-overlap").addEventListener("change", async (event) => {
      showOverlap = event.target.checked;
      try {
        await loadForecast();
      } catch (error) {
        toast(`加载失败：${error.message}`);
      }
    });

    root.querySelector("#planner-refresh").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api(`${API}/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force: true }),
        });
        const counts = result.counts || {};
        toast(
          `已刷新：预测 ${counts.predicted || 0} · 活动 ${counts.events || 0}` +
            (result.calibration?.pairs ? ` · 偏差按最近实测 +${result.calibration.used_days} 天` : ""),
        );
        await loadForecast();
        await loadStatus();
      } catch (error) {
        toast(`刷新失败：${error.message}`);
      } finally {
        button.disabled = false;
      }
    });

    forecastEl.addEventListener("click", (event) => {
      const more = event.target.closest("[data-more]");
      if (more) {
        limiter.toggle(more.dataset.more);
        renderForecast();
        return;
      }
      const box = event.target.closest("input[data-pool]");
      if (box) {
        const uid = box.dataset.pool;
        if (box.checked) selected.set(uid, { pulls: 0 });
        else selected.delete(uid);
        renderTargets();
      }
    });

    targetsEl.addEventListener("input", (event) => {
      const input = event.target.closest("input[data-pulls]");
      if (!input) return;
      const uid = input.dataset.pulls;
      if (selected.has(uid)) selected.set(uid, { pulls: Number(input.value) || 0 });
    });

    targetsEl.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove]");
      if (!remove) return;
      selected.delete(remove.dataset.remove);
      renderTargets();
      renderForecast();
    });

    root.querySelector("#plan-evaluate").addEventListener("click", runEvaluate);

    root.querySelector("#plan-save").addEventListener("click", async () => {
      const body = payload();
      try {
        const saved = planId
          ? await api(`${API}/plans/${planId}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            })
          : await api(`${API}/plans`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            });
        planId = saved.id;
        await loadPlans();
        toast("方案已保存");
      } catch (error) {
        toast(`保存失败：${error.message}`);
      }
    });

    root.querySelector("#plan-reset").addEventListener("click", () => {
      selected.clear();
      evaluation = null;
      planId = null;
      caratsEl.value = 0;
      ticketsEl.value = 0;
      incomeEl.value = 0;
      renderTargets();
      renderResult();
      renderForecast();
      renderSaved();
    });

    savedEl.addEventListener("click", async (event) => {
      const loadButton = event.target.closest("[data-load]");
      const deleteButton = event.target.closest("[data-delete]");
      try {
        if (loadButton) {
          const data = await api(`${API}/plans/${loadButton.dataset.load}`);
          planId = data.plan.id;
          caratsEl.value = data.plan.carats;
          ticketsEl.value = data.plan.tickets;
          incomeEl.value = data.plan.monthly_income;
          selected.clear();
          for (const target of data.plan.targets || []) {
            if (poolsByUid().has(target.pool_uid)) {
              selected.set(target.pool_uid, { pulls: target.pulls });
            }
          }
          evaluation = data.evaluation;
          renderTargets();
          renderResult();
          renderForecast();
          renderSaved();
          toast(`已加载方案「${data.plan.name}」`);
        } else if (deleteButton) {
          await api(`${API}/plans/${deleteButton.dataset.delete}`, { method: "DELETE" });
          if (Number(deleteButton.dataset.delete) === planId) planId = null;
          await loadPlans();
          toast("方案已删除");
        }
      } catch (error) {
        toast(`操作失败：${error.message}`);
      }
    });

    // 首次加载：先拿 status（给口径提示），再拿预测与方案；失败只影响自己那块
    try {
      await loadStatus();
    } catch (error) {
      hintEl.textContent = `读取状态失败：${error.message}`;
    }
    try {
      await loadForecast();
    } catch (error) {
      forecastEl.innerHTML = `<div class="empty">加载预测失败：${escapeHtml(error.message)}</div>`;
    }
    try {
      await loadPlans();
    } catch (error) {
      savedEl.innerHTML = `<div class="empty">读取已存方案失败：${escapeHtml(error.message)}</div>`;
    }
    renderTargets();

    return () => {};
  },
};
