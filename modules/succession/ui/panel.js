const API = "/api/succession";

function shortTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad2 = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours(),
  )}:${pad2(date.getMinutes())}`;
}

function rankPill(rank, escapeHtml) {
  if (!rank || !rank.label) return "";
  const tone = rank.rank === 3 ? "ok" : rank.rank === 2 ? "info" : "muted";
  return `<span class="pill ${tone}">${escapeHtml(rank.label)} · ${escapeHtml(
    String(rank.range || ""),
  )}</span>`;
}

function groupRows(groups, escapeHtml) {
  if (!groups || !groups.length) {
    return `<div class="empty">两人没有同时存在的关系组（点数为 0）</div>`;
  }
  return `<div class="list grid">${groups
    .map(
      (group) => `
      <div class="list-row">
        <div>
          <div class="name">关系组 ${escapeHtml(String(group.relation_type))}</div>
          <div class="meta">该组共 ${escapeHtml(String(group.size))} 人 · 组编号在主数据里没有名称字典</div>
        </div>
        <span class="pill ok nowrap">+${escapeHtml(String(group.point))}</span>
      </div>`,
    )
    .join("")}</div>`;
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast } = ctx;
    let status = null;
    let charas = [];
    let result = null;

    root.innerHTML = `
      <h1 class="page-title">相性</h1>
      <p class="page-desc">选两只马娘，看她们能带来多少相性分。技术口径（关系组编号、适性缺失等）都收在下面的「数据来源」与「口径说明」里。</p>

      <section class="card">
        <div class="toolbar">
          <button class="ghost" id="succ-refresh">刷新相性数据</button>
          <span class="hint" id="succ-mdb"></span>
          <span class="hint" id="succ-stale"></span>
        </div>
        <details style="margin-top:10px">
          <summary>数据来源与导入信息</summary>
          <dl class="kv" id="succ-source"></dl>
        </details>
        <div class="answer" id="succ-refresh-result" style="margin-top:12px" hidden></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>两人相性</h2>
        <div class="toolbar">
          <label>马娘 A <select id="succ-a" style="min-width:160px"></select></label>
          <label>马娘 B <select id="succ-b" style="min-width:160px"></select></label>
          <button class="primary" id="succ-calc">计算</button>
          <span class="hint" id="succ-pair-warn"></span>
        </div>
        <div id="succ-pair"></div>
      </section>

      <section class="card" style="margin-top:16px">
        <h2>六人继承布局（可选）</h2>
        <div class="toolbar">
          <label>目标 <select id="succ-target" style="min-width:150px"></select></label>
          <label>父母 1 <select id="succ-p1" style="min-width:150px"></select></label>
          <label>父母 2 <select id="succ-p2" style="min-width:150px"></select></label>
        </div>
        <div class="toolbar" style="margin-top:8px">
          <label>祖父母 1 <select id="succ-g1" style="min-width:120px"></select></label>
          <label>祖父母 2 <select id="succ-g2" style="min-width:120px"></select></label>
          <label>祖父母 3 <select id="succ-g3" style="min-width:120px"></select></label>
          <label>祖父母 4 <select id="succ-g4" style="min-width:120px"></select></label>
        </div>
        <div class="toolbar" style="margin-top:10px">
          <button class="primary" id="succ-layout">计算布局</button>
          <span class="hint" id="succ-layout-hint"></span>
        </div>
        <div id="succ-layout-result"></div>
      </section>

      <section class="card" style="margin-top:16px">
        <details>
          <summary>口径说明（不是游戏内最终相性，不含适性）</summary>
          <ul class="hint" style="margin:0" id="succ-notes"></ul>
        </details>
      </section>
    `;

    const sourceEl = root.querySelector("#succ-source");
    const mdbEl = root.querySelector("#succ-mdb");
    const staleEl = root.querySelector("#succ-stale");
    const refreshResultEl = root.querySelector("#succ-refresh-result");
    const pairEl = root.querySelector("#succ-pair");
    const pairWarnEl = root.querySelector("#succ-pair-warn");
    const layoutEl = root.querySelector("#succ-layout-result");
    const layoutHintEl = root.querySelector("#succ-layout-hint");
    const notesEl = root.querySelector("#succ-notes");
    const calcBtn = root.querySelector("#succ-calc");
    const layoutBtn = root.querySelector("#succ-layout");

    const selects = {
      a: root.querySelector("#succ-a"),
      b: root.querySelector("#succ-b"),
      target: root.querySelector("#succ-target"),
      p1: root.querySelector("#succ-p1"),
      p2: root.querySelector("#succ-p2"),
      g1: root.querySelector("#succ-g1"),
      g2: root.querySelector("#succ-g2"),
      g3: root.querySelector("#succ-g3"),
      g4: root.querySelector("#succ-g4"),
    };

    function setResult(text) {
      refreshResultEl.textContent = text || "";
      refreshResultEl.hidden = !text;
    }

    // 选错组合时直接禁用按钮并说明原因，比点下去再报 422 友好
    function syncPairButton() {
      const same = selects.a.value === selects.b.value;
      calcBtn.disabled = same;
      pairWarnEl.textContent = same ? "马娘 A 与 B 不能是同一角色（衣装不同也算同一角色）" : "";
    }

    function syncLayoutButton() {
      const picked = [selects.target.value, selects.p1.value, selects.p2.value];
      const distinct = new Set(picked).size === 3 && picked.every(Boolean);
      layoutBtn.disabled = !distinct;
      layoutHintEl.textContent = distinct ? "" : "目标与两位父母必须是三个不同角色";
    }

    // 改了选择就把上一次的结果清掉，避免上下两块对不上
    function resetPair() {
      pairEl.innerHTML = "";
      syncPairButton();
    }

    function resetLayout() {
      layoutEl.innerHTML = "";
      syncLayoutButton();
    }

    function fillSelects() {      const options = charas
        .map((row) => `<option value="${row.chara_id}">${escapeHtml(row.name)}</option>`)
        .join("");
      for (const [key, el] of Object.entries(selects)) {
        const blank = key.startsWith("g");
        el.innerHTML = (blank ? `<option value="">（不填）</option>` : "") + options;
      }
      // 默认给两组不同的马娘，避免一打开就是同一个角色
      if (charas.length > 1) {
        selects.a.value = charas[0].chara_id;
        selects.b.value = charas[1].chara_id;
        selects.target.value = charas[0].chara_id;
        selects.p1.value = charas[1].chara_id;
        selects.p2.value = charas[2]?.chara_id ?? charas[1].chara_id;
      }
      syncPairButton();
      syncLayoutButton();
    }

    function renderSource() {
      const counts = status.counts || {};
      mdbEl.textContent = status.mdb?.exists
        ? `master.mdb ${status.mdb.size_mb} MB`
        : "未找到 master.mdb";
      // stale 是可以立刻处理的，留在外面；其余条数收进折叠区
      staleEl.innerHTML = status.stale
        ? '<span class="pill warn">master.mdb 已更新，可刷新</span>'
        : "";
      const catalog = status.catalog || null;
      const ranks = (status.ranks || [])
        .map((row) => `${escapeHtml(row.label)} ${escapeHtml(row.range)}`)
        .join(" · ");
      sourceEl.innerHTML = `
        <dt>关系组</dt><dd>${counts.relations || 0} 个 · 成员 ${counts.members || 0} 条 · 角色 ${
          counts.charas || 0
        } 个</dd>
        <dt>等级区间</dt><dd>${ranks || "—"}</dd>
        <dt>导入来源</dt><dd>${escapeHtml(catalog?.source || "未导入")}${
          catalog ? ` · 导入于 ${escapeHtml(shortTime(catalog.imported_at))}` : ""
        }</dd>
        <dt>数据版本</dt><dd>${escapeHtml(status.data_version?.version || "未知")}</dd>
      `;
    }

    function renderNotes() {
      notesEl.innerHTML = (status.notes || [])
        .map((note) => `<li>${escapeHtml(note)}</li>`)
        .join("");
    }

    function renderPair(data) {
      if (!data) {
        pairEl.innerHTML = "";
        return;
      }
      // 逐组的编号 / 人数 / 点数属于内部推导过程，默认收起，只留分数与等级
      const breakdown = (data.groups || [])
        .map((group) => `${group.point}`)
        .join(" + ");
      pairEl.innerHTML = `
        <dl class="kv">
          <dt>组合</dt><dd>${escapeHtml(data.a.name)} × ${escapeHtml(data.b.name)}</dd>
          <dt>分数</dt><dd>${data.total}</dd>
          <dt>等级</dt><dd>${rankPill(data.rank, escapeHtml)}</dd>
        </dl>
        <details style="margin-top:12px">
          <summary>共享关系组明细（${data.group_count} 个）</summary>
          <p class="hint" style="margin:0 0 8px">点数连加式：${escapeHtml(breakdown || "0")}</p>
          ${groupRows(data.groups, escapeHtml)}
          <p class="hint">${escapeHtml((data.notes || [])[0] || "")}</p>
        </details>
      `;
    }

    function renderLayout(data) {
      if (!data) {
        layoutEl.innerHTML = "";
        return;
      }
      const rows = data.slots
        .map(
          (slot) => `
          <tr>
            <td class="mono">${escapeHtml(slot.slot)}</td>
            <td>${escapeHtml(slot.label)}</td>
            <td class="mono">${slot.groups.length}</td>
            <td class="mono">+${slot.subtotal}${
              slot.zeroed ? ' <span class="tag level-d">同角色记 0</span>' : ""
            }</td>
          </tr>`,
        )
        .join("");
      layoutEl.innerHTML = `
        <dl class="kv">
          <dt>布局</dt><dd>${escapeHtml(data.target.name)} ← ${escapeHtml(
            data.parents.map((row) => row.name).join(" / "),
          )} · 祖父母 ${
            data.grandparents.length
              ? escapeHtml(data.grandparents.map((row) => row.name).join(" / "))
              : "未填写"
          }</dd>
          <dt>分数</dt><dd>${data.total}</dd>
          <dt>等级</dt><dd>${rankPill(data.rank, escapeHtml)}</dd>
        </dl>
        <table class="seg-table" style="margin-top:12px">
          <thead><tr><th>位置</th><th>组合</th><th>关系组</th><th>小计</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${
          data.missing_grandparents
            ? `<p class="hint">还有 ${data.missing_grandparents} 个祖父母位未填写，总分只会偏低。</p>`
            : ""
        }
        <details style="margin-top:10px">
          <summary>口径说明（社区计算器口径，不是官方公式）</summary>
          <p class="hint" style="margin:0">${escapeHtml((data.notes || [])[0] || "")}</p>
        </details>
      `;
    }

    async function load() {
      status = await api(`${API}/status`);
      renderSource();
      renderNotes();
      const listing = await api(`${API}/charas?limit=500`);
      charas = listing.items || [];
      fillSelects();
    }

    async function calcPair() {
      const a = Number(selects.a.value);
      const b = Number(selects.b.value);
      try {
        result = await api(`${API}/pairs?a=${a}&b=${b}`);
        renderPair(result);
      } catch (error) {
        pairEl.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
      }
    }

    async function calcLayout() {
      const params = new URLSearchParams({
        target: selects.target.value,
        parent1: selects.p1.value,
        parent2: selects.p2.value,
      });
      for (const key of ["g1", "g2", "g3", "g4"]) {
        if (selects[key].value) params.set(key, selects[key].value);
      }
      try {
        renderLayout(await api(`${API}/layout?${params.toString()}`));
      } catch (error) {
        layoutEl.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
      }
    }

    root.querySelector("#succ-calc").addEventListener("click", calcPair);
    root.querySelector("#succ-layout").addEventListener("click", calcLayout);
    selects.a.addEventListener("change", resetPair);
    selects.b.addEventListener("change", resetPair);
    for (const key of ["target", "p1", "p2"]) {
      selects[key].addEventListener("change", resetLayout);
    }
    root.querySelector("#succ-refresh").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      setResult("正在从 master.mdb 导入相性关系…");
      button.disabled = true;
      try {
        const res = await api(`${API}/refresh?force=true`, { method: "POST" });
        setResult(
          res.imported
            ? `已导入：关系组 ${res.summary.relations} 个 / 成员 ${res.summary.members} 条`
            : res.reason || "本地已是最新",
        );
        await load();
        await calcPair();
      } catch (error) {
        setResult(`导入失败：${error.message}`);
      } finally {
        button.disabled = false;
      }
    });

    try {
      await load();
      await calcPair();
    } catch (error) {
      toast(error.message, "error");
    }
  },
};
