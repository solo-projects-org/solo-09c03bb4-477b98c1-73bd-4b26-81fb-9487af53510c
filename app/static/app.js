const $ = (sel) => document.querySelector(sel);

function badge(status) {
  const label = {
    current: "现行",
    stale: "待重算",
    uncomputed: "未计算",
    pending: "排队中",
    running: "运行中",
    done: "完成",
    failed: "失败",
  }[status] || status;
  return `<span class="badge ${status}">${label}</span>`;
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function pairLabel(row) {
  return `${esc(row.from_station)} → ${esc(row.to_station)}`;
}

function signed(n) {
  return n > 0 ? `+${n}` : String(n);
}

function renderChanges(changes) {
  if (!changes) {
    return `<h3>客流变动说明</h3><div class="notice stale">本日客流变动说明不可用（人数与说明必须同时发布）。</div>`;
  }
  const head = `
    <h3>客流变动说明 <span class="small muted">（按进站 UTC 日）</span></h3>
    <p class="small muted">
      对照上一次现行发布${
        changes.has_baseline
          ? `（批次 #${esc(changes.baseline_batch_id)}，计算于 ${esc(changes.baseline_computed_at)}）`
          : ""
      }
      与本次发布（计算于 ${esc(changes.compared_at)}）。
    </p>`;
  if (!changes.has_baseline) {
    return head + `<div class="notice uncomputed">本日首次发布，没有可对照的上一版现行结果。</div>`;
  }

  const changedPairs = changes.pairs.filter((p) => p.delta !== 0);
  const unchangedPairs = changes.pairs.length - changedPairs.length;
  const pairRows = changes.pairs.map((p) => `
    <tr class="${p.delta === 0 ? "muted-row" : ""}">
      <td>${pairLabel(p)}</td>
      <td class="num">${p.before_trips}</td>
      <td class="num">${p.after_trips}</td>
      <td class="num ${p.delta > 0 ? "up" : p.delta < 0 ? "down" : ""}">${signed(p.delta)}</td>
    </tr>`).join("");

  const journeyRows = changes.journeys.map((j) => `
    <tr>
      <td><span class="badge ${j.kind}">${j.kind === "added" ? "新增" : "撤去"}</span></td>
      <td>${pairLabel(j)}</td>
      <td class="mono">${esc(j.entry_event_no)} → ${esc(j.exit_event_no)}</td>
      <td class="mono">${esc(j.card_id)}</td>
      <td class="mono">${esc(j.exit_date)}${j.exit_date !== changes.__date ? ' <span class="small">跨日</span>' : ""}</td>
    </tr>`).join("");

  const offsetNotice = changes.journeys.length && changedPairs.length === 0
    ? `<div class="notice uncomputed">站间人数净增减为 0，但有 ${changes.journeys.length} 条行程被替换（${changes.added_count} 新增 / ${changes.removed_count} 撤去），已逐条列出。</div>`
    : "";

  return head + offsetNotice + `
    <div class="stat-row">
      <div class="stat"><div class="k">变动区间</div><div class="v">${changedPairs.length}</div></div>
      <div class="stat"><div class="k">新增行程</div><div class="v">${changes.added_count}</div></div>
      <div class="stat"><div class="k">撤去行程</div><div class="v">${changes.removed_count}</div></div>
    </div>
    <h4>各站间人数净增减</h4>
    <table class="grid">
      <thead><tr><th>区间</th><th class="num">上次</th><th class="num">本次</th><th class="num">净增减</th></tr></thead>
      <tbody>${pairRows || '<tr><td colspan="4" class="empty">当日无站间人数</td></tr>'}</tbody>
    </table>
    ${unchangedPairs ? `<p class="small muted">另有 ${unchangedPairs} 个区间人数未变。</p>` : ""}
    <h4>新增 / 撤去行程</h4>
    <table class="grid">
      <thead><tr><th>类型</th><th>区间</th><th>进出站事件号</th><th>卡号</th><th>出站日</th></tr></thead>
      <tbody>${journeyRows || '<tr><td colspan="5" class="empty">本次行程明细无变化</td></tr>'}</tbody>
    </table>`;
}

function renderDay(data) {
  const panel = $("#day-panel");
  panel.hidden = false;
  const head = `
    <div class="panel-head">
      <h3>${esc(data.date)} · UTC 服务日</h3>
      ${badge(data.status)}
      ${data.computed_at ? `<span class="mono small">计算于 ${esc(data.computed_at)}</span>` : ""}
    </div>`;

  if (data.status !== "current") {
    const msg = data.status === "stale"
      ? `结果已过期，不能用于排班。上次的客流变动说明同时作废。${data.last_error ? " 原因：" + esc(data.last_error) : ""} 请在下方触发重算。`
      : "该日尚未计算完成。";
    panel.innerHTML = head + `<div class="notice ${data.status}">${msg}</div>` +
      `<button class="recompute" data-date="${esc(data.date)}">重算 ${esc(data.date)}</button>`;
    bindRecompute();
    return;
  }

  const pairs = data.station_pairs.map((row) => `
    <tr><td>${pairLabel(row)}</td>
        <td class="num">${row.trips}</td></tr>`).join("");
  const pending = data.pending.map((row) => `
    <tr><td class="mono">${esc(row.card_id)}</td>
        <td class="mono">${esc(row.tap_time)}</td>
        <td>${esc(row.station)}</td>
        <td>${row.direction === "in" ? "进站" : "出站"}</td>
        <td class="small">${esc(row.reason)}</td></tr>`).join("");

  const changes = data.changes ? { ...data.changes, __date: data.date } : null;
  panel.innerHTML = head + `
    <div class="stat-row">
      <div class="stat"><div class="k">配对行程</div><div class="v">${data.trip_total}</div></div>
      <div class="stat"><div class="k">待核刷卡</div><div class="v">${data.pending_total}</div></div>
      <div class="stat"><div class="k">站间流向</div><div class="v">${data.station_pairs.length}</div></div>
    </div>
    <div class="cols">
      <div>
        <h3>站间人数（按进站日）</h3>
        <table class="grid">
          <thead><tr><th>区间</th><th class="num">人数</th></tr></thead>
          <tbody>${pairs || '<tr><td colspan="2" class="empty">当日无配对行程</td></tr>'}</tbody>
        </table>
      </div>
      <div>
        <h3>待核记录（不计入客流）</h3>
        <table class="grid">
          <thead><tr><th>卡号</th><th>时刻 (UTC)</th><th>站点</th><th>方向</th><th>原因</th></tr></thead>
          <tbody>${pending || '<tr><td colspan="5" class="empty">当日无待核记录</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    <section class="changes">${renderChanges(changes)}</section>`;
  bindRecompute();
}

async function loadDay(date) {
  document.querySelectorAll(".day-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.date === date);
  });
  const res = await fetch(`/api/days/${date}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    $("#day-panel").hidden = false;
    $("#day-panel").innerHTML = `<div class="notice stale">${esc(body.detail || "加载失败")}</div>`;
    return;
  }
  renderDay(await res.json());
}

document.querySelectorAll(".day-tab").forEach((el) => {
  el.addEventListener("click", () => loadDay(el.dataset.date));
});

function bindRecompute() {
  document.querySelectorAll(".recompute").forEach((el) => {
    el.addEventListener("click", async () => {
      el.disabled = true;
      const res = await fetch(`/api/recompute?days=${encodeURIComponent(el.dataset.date)}`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) alert(body.detail || "重算失败");
      await refreshTabs();
      await loadDay(el.dataset.date);
    });
  });
}

async function refreshTabs() {
  const res = await fetch("/api/days");
  if (!res.ok) return;
  const { days } = await res.json();
  const tabs = $("#day-tabs");
  const current = document.querySelector(".day-tab.active")?.dataset.date;
  tabs.innerHTML = days.length
    ? days.map((d) => `
      <button class="day-tab ${d.status === "stale" ? "stale" : ""} ${d.date === current ? "active" : ""}" data-date="${esc(d.date)}">
        <span class="date">${esc(d.date)}</span>
        <span class="badge ${d.status}">${d.status === "current" ? "现行" : "待重算"}</span>
      </button>`).join("")
    : '<p class="empty">尚无数据，请先上传。</p>';
  tabs.querySelectorAll(".day-tab").forEach((el) => {
    el.addEventListener("click", () => loadDay(el.dataset.date));
  });
}

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = $("#csv");
  const result = $("#upload-result");
  const btn = $("#upload-btn");
  if (!fileInput.files.length) return;
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  btn.disabled = true;
  result.hidden = false;
  result.className = "result";
  result.textContent = "上传并重算中…";
  try {
    const res = await fetch("/api/uploads", { method: "POST", body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      result.className = "result error";
      result.textContent = body.detail || `上传失败（HTTP ${res.status}）`;
      return;
    }
    const states = Object.entries(body.day_states || {})
      .map(([day, status]) => `${day}: ${status === "current" ? "已转为现行" : "待重算"}`).join("\n");
    result.textContent =
      `批次 #${body.batch_id} 已受理：${body.rows} 行，新增 ${body.inserted}，忽略 ${body.ignored}。\n` +
      `涉及日期：${(body.affected_days || []).join(", ") || "无"}\n${states}`;
    await refreshTabs();
    if (body.affected_days?.length) await loadDay(body.affected_days[0]);
  } catch (err) {
    result.className = "result error";
    result.textContent = `网络错误：${err}`;
  } finally {
    btn.disabled = false;
  }
});
