const API = "/api/knowledge";

function pad2(value) {
  return String(value).padStart(2, "0");
}

// 文档导入时间以 UTC 入库，按系统时区换算后再显示
function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19).replace("T", " ");
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
  );
}

export default {
  async mount(root, ctx) {
    const { api, escapeHtml, toast, createLimiter } = ctx;
    const limiter = createLimiter(6);
    let stats = null;
    let documents = [];
    let history = [];

    root.innerHTML = `
      <h1 class="page-title">知识库问答</h1>
      <p class="page-desc">上传攻略、公告、Wiki 导出文档；回答会带上资料引用，并标注当前数据版本。</p>
      <div class="grid two">
        <section class="card">
          <h2>文档</h2>
          <div class="toolbar">
            <label class="file-picker" for="kb-files">选择文件</label>
            <input type="file" id="kb-files" class="visually-hidden" multiple accept=".pdf,.md,.markdown,.txt" />
            <span class="hint" id="kb-files-label">未选择文件</span>
            <button class="primary" id="kb-upload">导入</button>
          </div>
          <div class="hint" id="kb-stats"></div>
          <div class="list" id="kb-docs"></div>
        </section>
        <section class="card">
          <h2>提问</h2>
          <div id="kb-llm-warning"></div>
          <textarea id="kb-question" class="full" rows="3" placeholder="例如：这次大赛的赛道适合什么跑法？"></textarea>
          <div class="toolbar" style="margin-top:10px">
            <button class="primary" id="kb-ask">提问</button>
            <button class="ghost" id="kb-clear">清空对话</button>
            <span class="hint" id="kb-model"></span>
          </div>
          <div class="answer" id="kb-answer">回答会显示在这里。</div>
          <div id="kb-structured"></div>
          <div class="sources" id="kb-sources"></div>
        </section>
      </div>
    `;

    const docsEl = root.querySelector("#kb-docs");
    const statsEl = root.querySelector("#kb-stats");
    const answerEl = root.querySelector("#kb-answer");
    const sourcesEl = root.querySelector("#kb-sources");
    const structuredEl = root.querySelector("#kb-structured");
    const questionEl = root.querySelector("#kb-question");
    const askButton = root.querySelector("#kb-ask");
    const uploadButton = root.querySelector("#kb-upload");
    const fileInput = root.querySelector("#kb-files");
    const filesLabel = root.querySelector("#kb-files-label");

    function renderDocs() {
      if (!documents.length) {
        docsEl.innerHTML = `<div class="empty">还没有导入文档</div>`;
        return;
      }
      docsEl.innerHTML = `${limiter
        .slice("docs", documents)
        .map(
          (doc) => `
        <div class="list-row">
          <div>
            <div class="name">${escapeHtml(doc.filename)}</div>
            <div class="meta">${doc.chunk_count} 块 · ${(doc.size_bytes / 1024).toFixed(1)} KB · ${escapeHtml(
              formatTime(doc.created_at),
            )}</div>
          </div>
          <button class="ghost" data-doc="${escapeHtml(doc.id)}">删除</button>
        </div>`,
        )
        .join("")}${limiter.more("docs", documents.length)}`;
      docsEl.querySelectorAll("button[data-doc]").forEach((button) => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await api(`${API}/documents/${button.dataset.doc}`, { method: "DELETE" });
            await refresh();
            toast("已删除文档");
          } catch (error) {
            toast(error.message, "error");
            button.disabled = false;
          }
        });
      });
    }

    async function refresh() {
      stats = await api(`${API}/stats`);
      documents = await api(`${API}/documents`);
      statsEl.textContent = `${stats.documents} 个文档 / ${stats.chunks} 块 · ${stats.embedding_model}`;
      const warning = root.querySelector("#kb-llm-warning");
      warning.innerHTML = stats.llm_configured
        ? ""
        : `<p class="hint" style="color:var(--danger)">未配置 DEEPSEEK_API_KEY：文档检索照常，但只有问到赛道 / 大赛 / 技能 / 卡池 / 图鉴这类结构化数据时会直接给出查库结果，其余问题无法生成回答。</p>`;
      askButton.disabled = false;
      askButton.title = stats.llm_configured ? "" : "未配置 DEEPSEEK_API_KEY：只会给出结构化查询结果";
      root.querySelector("#kb-model").textContent = stats.llm_configured
        ? `${stats.llm_model} · 数据版本 ${stats.data_version}`
        : `数据版本 ${stats.data_version}`;
      renderDocs();
    }

    const MODE_LABEL = {
      ok: "结构化数据 + 文档",
      structured_only: "只有结构化数据（未配置 LLM）",
      documents_only: "只有文档（结构化数据没命中）",
    };

    function renderStructured(result) {
      const data = result.structured || {};
      const facts = data.facts || [];
      const misses = data.misses || [];
      const errors = data.errors || [];
      const badge = MODE_LABEL[result.mode] || result.mode || "";
      const blocks = facts
        .map(
          (fact) => `
        <div class="list-row">
          <div>
            <div class="name">${escapeHtml(fact.label)}</div>
            <div class="meta">
              <span class="mono">${escapeHtml(fact.endpoint)}</span>
              <span>· 表 ${escapeHtml(fact.tables)}</span>
              <span>· 数据版本 ${escapeHtml(fact.data_version)}</span>
            </div>
            <div class="meta clamp">${escapeHtml((fact.lines || []).join("；"))}</div>
          </div>
          <span class="pill ok nowrap">${escapeHtml(String((fact.lines || []).length))} 条</span>
        </div>`,
        )
        .join("");
      const notes = [
        ...misses.map((text) => `<li>没查到：${escapeHtml(text)}</li>`),
        ...errors.map((text) => `<li>接口失败：${escapeHtml(text)}</li>`),
      ].join("");
      structuredEl.innerHTML = `
        <h3>依据（${badge}）</h3>
        ${facts.length ? `<div class="list">${blocks}</div>` : `<div class="empty">结构化数据没命中，下面的回答来自文档</div>`}
        ${notes ? `<ul class="hint" style="margin-top:8px">${notes}</ul>` : ""}
      `;
    }

    async function ask() {
      const question = questionEl.value.trim();
      if (!question) return;
      askButton.disabled = true;
      answerEl.textContent = "思考中…";
      sourcesEl.innerHTML = "";
      structuredEl.innerHTML = "";
      try {
        const result = await api(`${API}/chat`, {
          method: "POST",
          body: JSON.stringify({ question, history }),
        });
        answerEl.textContent = result.answer;
        renderStructured(result);
        sourcesEl.innerHTML = result.sources
          .map(
            (source) => `
          <div class="source">
            [${source.index}] <span class="file">${escapeHtml(source.filename)}</span>
            ${source.heading ? ` · ${escapeHtml(source.heading)}` : ""}
            · 相似度 ${source.score.toFixed(3)}
            <div>${escapeHtml(source.snippet.slice(0, 220))}</div>
          </div>`,
          )
          .join("");
        history.push({ role: "user", content: question });
        history.push({ role: "assistant", content: result.answer });
        history = history.slice(-8);
      } catch (error) {
        answerEl.textContent = `请求失败：${error.message}`;
      } finally {
        askButton.disabled = false;
      }
    }

    fileInput.addEventListener("change", () => {
      const names = Array.from(fileInput.files || []).map((file) => file.name);
      filesLabel.textContent = names.length ? names.join("、") : "未选择文件";
    });

    // 文档列表的「展开其余 N 条」（删除按钮的绑定在 renderDocs 里逐条做）
    docsEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-more]");
      if (!button || button.dataset.more !== "docs") return;
      limiter.toggle("docs");
      renderDocs();
    });

    uploadButton.addEventListener("click", async () => {
      const files = Array.from(fileInput.files || []);
      if (!files.length) {
        toast("先选择文件", "error");
        return;
      }
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      uploadButton.disabled = true;
      uploadButton.textContent = "导入中…";
      try {
        const result = await api(`${API}/documents`, { method: "POST", body: form });
        const skipped = result.skipped || [];
        toast(`已导入 ${result.documents.length} 个文档${skipped.length ? `，跳过 ${skipped.length} 个` : ""}`);
        fileInput.value = "";
        filesLabel.textContent = "未选择文件";
        await refresh();
      } catch (error) {
        toast(error.message, "error");
      } finally {
        uploadButton.disabled = false;
        uploadButton.textContent = "导入";
      }
    });

    askButton.addEventListener("click", ask);
    questionEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        ask();
      }
    });
    root.querySelector("#kb-clear").addEventListener("click", () => {
      history = [];
      answerEl.textContent = "回答会显示在这里。";
      sourcesEl.innerHTML = "";
      structuredEl.innerHTML = "";
    });

    await refresh();
    return () => {
      history = [];
    };
  },
};
