// 面板契约：导出默认对象，实现 mount(root, ctx)。
// ctx 提供 api / escapeHtml / toast / state。

export default {
  async mount(root, ctx) {
    const { api, escapeHtml } = ctx;
    root.innerHTML = `
      <h1 class="page-title">示例模块</h1>
      <p class="page-desc">复制 modules/_template 开始开发新功能。</p>
      <section class="card"><div id="demo">加载中…</div></section>
    `;
    try {
      const result = await api("/api/example/ping");
      root.querySelector("#demo").innerHTML = `模块 <b>${escapeHtml(result.module)}</b> 正常，数据版本 ${escapeHtml(
        result.data_version,
      )}`;
    } catch (error) {
      root.querySelector("#demo").textContent = `调用失败：${error.message}`;
    }
    // 返回的函数会在离开页面时执行，用于清理定时器/事件监听
    return () => {};
  },
};
