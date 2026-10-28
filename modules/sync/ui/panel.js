const API = "/api/sync";

function pad2(value) {
  return String(value).padStart(2, "0");
}

// 快照时间以 UTC 入库，按系统时区换算后再显示，避免差 8 小时
function formatTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19).replace("T", " ");
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
  );
}

function datasetLine(datasets) {
  const items = Object.entries(datasets || {});
  if (!items.length) return "";
  return items.map(([key, value]) => `${key} ${value}`).join(" · ");
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, refreshInfo, createLimiter } = ctx;
    const limiter = createLimiter(6);
    let status = null;
    let history = [];
    let check = null;
    let device = null;

    root.innerHTML = `
      <h1 class="page-title">数据同步</h1>
      <p class="page-desc">数据来自你本机已安装的游戏客户端。导入后图鉴与知识库立刻反映新版本，每次导入都会留一条快照。</p>
      <div class="grid two">
        <section class="card">
          <h2>当前数据版本</h2>
          <dl class="kv" id="sync-version"></dl>
          <div class="hint" id="sync-extra" style="margin-top:12px"></div>
        </section>
        <section class="card">
          <h2>从本机客户端更新</h2>
          <div class="toolbar" style="margin-bottom:0">
            <button class="primary" id="sync-check">检查客户端</button>
            <button class="ghost" id="sync-apply" disabled>导入这一版</button>
          </div>
          <div class="hint" id="sync-device" style="margin-top:10px"></div>
          <div class="hint" id="sync-check-result" style="margin-top:6px"></div>
        </section>
      </div>
      <div class="grid" style="margin-top:16px">
        <section class="card">
          <h2>从文件导入</h2>
          <div class="toolbar">
            <label class="file-picker" for="sync-file">选择 master.mdb</label>
            <input type="file" id="sync-file" class="visually-hidden" accept=".mdb,.db,.sqlite" />
            <span class="hint" id="sync-file-label">未选择文件</span>
            <input type="text" id="sync-game-version" placeholder="游戏版本（可选）" style="width:150px" />
            <input type="text" id="sync-data-version" placeholder="数据版本（可选）" style="width:190px" />
            <button class="primary" id="sync-upload">导入</button>
          </div>
          <div class="answer" id="sync-result">导入结果会显示在这里。</div>
        </section>
        <section class="card">
          <h2>历史快照</h2>
          <div class="hint" id="sync-history-hint"></div>
          <div class="list" id="sync-history"></div>
        </section>
      </div>
    `;

    const versionEl = root.querySelector("#sync-version");
    const extraEl = root.querySelector("#sync-extra");
    const deviceEl = root.querySelector("#sync-device");
    const checkEl = root.querySelector("#sync-check-result");
    const resultEl = root.querySelector("#sync-result");
    const historyEl = root.querySelector("#sync-history");
    // 长列表的「展开其余 N 条」
    historyEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-more]");
      if (!button || button.dataset.more !== "history") return;
      limiter.toggle("history");
      renderHistory();
    });
    const historyHintEl = root.querySelector("#sync-history-hint");
    const fileInput = root.querySelector("#sync-file");
    const fileLabel = root.querySelector("#sync-file-label");
    const checkButton = root.querySelector("#sync-check");
    const applyButton = root.querySelector("#sync-apply");
    const uploadButton = root.querySelector("#sync-upload");
    const gameVersionEl = root.querySelector("#sync-game-version");
    const dataVersionEl = root.querySelector("#sync-data-version");

    function renderStatus() {
      const data = status?.data_version || {};
      const rows = [
        ["版本", data.version || "未知"],
        ["游戏版本", data.game_version || "未记录"],
        ["更新于", formatTime(data.updated_at)],
        ["来源", data.source || "未记录"],
        ["数据指纹", status?.sha256 ? `sha256:${status.sha256}` : "无"],
      ];
      versionEl.innerHTML = rows
        .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`)
        .join("");
      extraEl.textContent = data.datasets
        ? datasetLine(data.datasets)
        : "还没有导入结构化数据，图鉴会是空的。";
      if (status?.kept_mdb) {
        extraEl.textContent += ` · 已留存 mdb ${(status.kept_mdb.size / 1024 / 1024).toFixed(1)} MB`;
      }
    }

    function renderDevice() {
      if (!device) {
        deviceEl.textContent = "";
        return;
      }
      deviceEl.textContent = device.available
        ? `adb ${device.adb}`
        : `本机客户端不可用：${device.message}`;
    }

    function renderCheck() {
      if (!check) {
        checkEl.textContent = "";
        applyButton.disabled = true;
        return;
      }
      const verdict = check.is_current
        ? "与本地库一致，无需导入"
        : "比本地库新，可以导入";
      checkEl.innerHTML = [
        `设备 ${escapeHtml(check.serial)} · 游戏 ${escapeHtml(check.game_version)}`,
        `mdb ${(check.remote_size / 1024 / 1024).toFixed(1)} MB · 修改于 ${escapeHtml(
          formatTime(check.remote_mtime),
        )}`,
        `sha256 ${escapeHtml(check.sha256.slice(0, 12))} · ${verdict}`,
        `体检 马娘 ${check.characters} · 协助卡 ${check.support_cards} · 技能 ${check.skills} · 文本 ${check.text_entries}`,
      ].join("<br />");
      applyButton.disabled = false;
      dataVersionEl.value = check.suggested_version || "";
      gameVersionEl.value = check.game_version || "";
    }

    function renderHistory() {
      historyHintEl.textContent = status?.snapshot_dir
        ? `${history.length} 条记录 · ${status.snapshot_dir}`
        : "";
      if (!history.length) {
        historyEl.innerHTML = `<div class="empty">还没有导入记录</div>`;
        return;
      }
      historyEl.innerHTML = `${limiter
        .slice("history", history)
        .map(
          (item) => `
        <div class="list-row">
          <div>
            <div class="name">${escapeHtml(item.version || "未知版本")}${
              item.game_version
                ? ` <span class="mono">游戏 ${escapeHtml(item.game_version)}</span>`
                : ""
            }</div>
            <div class="meta">${escapeHtml(formatTime(item.imported_at))} · ${escapeHtml(
              item.via || "unknown",
            )} · ${escapeHtml(item.source || "")}</div>
            <div class="meta clamp">${escapeHtml(datasetLine(item.datasets))}</div>
          </div>
        </div>`,
        )
        .join("")}${limiter.more("history", history.length)}`;
    }

    function reportResult(result) {
      if (!result.imported) {
        resultEl.textContent = `${result.reason}（sha256:${String(result.sha256 || "").slice(0, 12)}）`;
        return;
      }
      const summary = result.summary || {};
      resultEl.textContent = [
        `已导入 ${summary.data_version}（游戏 ${summary.game_version || "未记录"}）`,
        `马娘 ${summary.characters} · 协助卡 ${summary.support_cards} · 技能 ${summary.skills} · 文本 ${summary.text_entries}`,
        `已写快照 data/snapshots/${result.snapshot?.version || ""}.json`,
        summary.missing_columns && Object.keys(summary.missing_columns).length
          ? `字段降级：${JSON.stringify(summary.missing_columns)}`
          : "",
      ]
        .filter(Boolean)
        .join("\n");
    }

    async function refresh() {
      status = await api(`${API}/status`);
      history = await api(`${API}/history?limit=20`);
      renderStatus();
      renderHistory();
    }

    async function afterImport(result) {
      reportResult(result);
      toast(result.imported ? "数据已更新" : result.reason);
      // 导入改了数据版本：清掉上一次的检查结果，并让侧栏徽标同步刷新
      check = null;
      renderCheck();
      await refresh();
      if (result.imported && refreshInfo) {
        try {
          await refreshInfo();
        } catch (error) {
          console.warn("刷新数据版本失败", error);
        }
      }
    }

    checkButton.addEventListener("click", async () => {
      checkButton.disabled = true;
      checkButton.textContent = "检查中…";
      checkEl.textContent = "正在从设备提取并体检，通常需要 10–30 秒。";
      applyButton.disabled = true;
      try {
        check = await api(`${API}/device/check`);
        renderCheck();
      } catch (error) {
        check = null;
        checkEl.textContent = `检查失败：${error.message}`;
      } finally {
        checkButton.disabled = false;
        checkButton.textContent = "检查客户端";
      }
    });

    applyButton.addEventListener("click", async () => {
      applyButton.disabled = true;
      applyButton.textContent = "导入中…";
      resultEl.textContent = "正在从设备重新提取并导入，请勿关闭窗口。";
      try {
        const result = await api(`${API}/android`, {
          method: "POST",
          body: JSON.stringify({ force: false }),
        });
        await afterImport(result);
      } catch (error) {
        resultEl.textContent = `导入失败：${error.message}`;
        toast(error.message, "error");
      } finally {
        applyButton.textContent = "导入这一版";
        applyButton.disabled = !check;
      }
    });

    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      fileLabel.textContent = file ? `${file.name}（${(file.size / 1024 / 1024).toFixed(1)} MB）` : "未选择文件";
    });

    uploadButton.addEventListener("click", async () => {
      const file = fileInput.files?.[0];
      if (!file) {
        toast("先选择 master.mdb", "error");
        return;
      }
      const form = new FormData();
      form.append("file", file);
      if (gameVersionEl.value.trim()) form.append("game_version", gameVersionEl.value.trim());
      if (dataVersionEl.value.trim()) form.append("data_version", dataVersionEl.value.trim());
      uploadButton.disabled = true;
      uploadButton.textContent = "导入中…";
      resultEl.textContent = "正在导入，请稍候。";
      try {
        const result = await api(`${API}/upload`, { method: "POST", body: form });
        fileInput.value = "";
        fileLabel.textContent = "未选择文件";
        await afterImport(result);
      } catch (error) {
        resultEl.textContent = `导入失败：${error.message}`;
        toast(error.message, "error");
      } finally {
        uploadButton.disabled = false;
        uploadButton.textContent = "导入";
      }
    });

    await refresh();
    try {
      device = await api(`${API}/device`);
      renderDevice();
    } catch (error) {
      deviceEl.textContent = `无法探测 adb：${error.message}`;
    }

    return () => {
      check = null;
      history = [];
    };
  },
};
