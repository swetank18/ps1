// Clutch UI — talks to the FastAPI backend, animates the live sweep trace.
const $ = (s) => document.querySelector(s);
const api = (p, opt) => fetch(p, opt).then((r) => r.json());

const EXAMPLES = [
  "finish internship application in 8 hours",
  "prep DSA interview tomorrow 6pm",
  "submit report Friday",
  "pay hostel fee",
];

const AGENT_ICON = { risk_sentinel: "🛰️", scheduler: "📅", intervenor: "⚡" };
const fmtRisk = (r) => (r >= 1 ? "critical" : r >= 0.3 ? "high" : r >= 0.1 ? "medium" : "low");

function relTime(iso) {
  if (!iso) return "no deadline";
  const d = new Date(iso), now = new Date();
  let s = (d - now) / 1000;
  const past = s < 0; s = Math.abs(s);
  const h = s / 3600;
  let out;
  if (h < 1) out = `${Math.round(s / 60)}m`;
  else if (h < 48) out = `${Math.round(h)}h`;
  else out = `${Math.round(h / 24)}d`;
  return past ? `${out} overdue` : `in ${out}`;
}
const clockTime = (iso) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });

// ---------- render ----------
function renderBoard(tasks) {
  $("#task-count").textContent = tasks.length;
  const board = $("#board");
  if (!tasks.length) {
    board.innerHTML = `<div class="no-tasks">No tasks yet — drop a brain-dump or load the demo.</div>`;
    return;
  }
  const accent = { critical: "var(--crit)", high: "var(--high)", medium: "var(--med)", low: "var(--low)" };
  board.innerHTML = tasks.map((t, i) => {
    const band = t.band || fmtRisk(t.risk || 0);
    const pct = Math.min((t.risk || 0) * 100, 100);
    const atRisk = i === 0 && (t.risk || 0) >= 0.1;
    const subs = (t.subtasks || []).map((s) => `<span class="subtask">${esc(s)}</span>`).join("");
    const conf = t.confidence != null
      ? `<span class="conf" title="how sure Clutch is — higher with a known deadline + estimated effort">◑ ${Math.round(t.confidence * 100)}% conf</span>` : "";
    const blocked = t.blocked_by
      ? `<div class="dep">⛓ blocked by <b>${esc(t.blocked_by.title)}</b> — inherits its risk</div>`
      : (t.depends_on_titles && t.depends_on_titles.length
          ? `<div class="dep ok">⛓ waits on ${t.depends_on_titles.map(esc).join(", ")}</div>` : "");
    return `<div class="task ${atRisk ? "at-risk" : ""}" style="--accent:${accent[band]}; animation-delay:${i * 0.04}s">
      <div class="task-top">
        <div class="task-title">${esc(t.title)}</div>
        <span class="badge ${band}">${band}</span>
      </div>
      <div class="task-meta">
        <span>⏳ <b>${relTime(t.deadline)}</b></span>
        <span>⚙️ <b>${t.effort_minutes ?? "—"} min</b></span>
        ${conf}
      </div>
      <div class="gauge"><i style="width:${pct}%"></i></div>
      ${blocked}
      ${subs ? `<div class="subtasks">${subs}</div>` : ""}
    </div>`;
  }).join("");
}

function renderPanels(s) {
  const iv = $("#interventions");
  iv.innerHTML = s.interventions.length
    ? s.interventions.map(renderIv).join("")
    : `<p class="muted">No interventions yet.</p>`;

  const bl = $("#blocks");
  bl.innerHTML = s.blocks.length
    ? s.blocks.map((b) => `<div class="blk"><div><div class="b-title">${esc(b.summary)}</div></div>
        <div class="b-time">${clockTime(b.start)}</div></div>`).join("")
    : `<p class="muted">No blocks reserved yet.</p>`;

  const h = $("#history");
  h.innerHTML = s.sweeps.length
    ? s.sweeps.map((sw) => `<div class="hist"><span class="h-dot ${sw.at_risk ? "act" : "clear"}"></span>
        <div><div>${esc(sw.summary)}</div><div class="h-traj">${sw.trajectory.join(" → ")}</div></div></div>`).join("")
    : `<p class="muted">No sweeps run yet.</p>`;

  renderLearning(s.learning);
}

function renderLearning(l) {
  const el = $("#learning");
  if (!el) return;
  if (!l || (!l.feedback_total && !l.interventions_total)) {
    el.innerHTML = `<p class="muted">Accept or dismiss an intervention to teach Clutch.</p>`;
    return;
  }
  const c = l.counts || {};
  const acc = c.accept || 0, total = (c.accept || 0) + (c.dismiss || 0) + (c.snooze || 0);
  const rate = total ? Math.round((acc / total) * 100) : 0;
  const prefs = Object.entries(l.prefs || {})
    .sort((a, b) => b[1].weight - a[1].weight)
    .map(([cat, p]) => {
      const dir = p.weight > 1.05 ? "up" : p.weight < 0.95 ? "down" : "flat";
      const arrow = dir === "up" ? "↑" : dir === "down" ? "↓" : "→";
      return `<div class="lp ${dir}"><span class="lp-cat">${esc(cat)}</span>
        <span class="lp-w">${arrow} ×${p.weight.toFixed(2)}</span>
        <span class="lp-n">${p.accepts}✓ / ${p.dismisses}✕ / ${p.snoozes}⏰</span></div>`;
    }).join("");
  el.innerHTML = `
    <div class="learn-stats">
      <div><b>${l.interventions_total}</b><span>actions taken</span></div>
      <div><b>${rate}%</b><span>you accepted</span></div>
      <div><b>${l.feedback_total}</b><span>signals</span></div>
    </div>
    ${prefs ? `<div class="learn-prefs">${prefs}</div>` : ""}`;
}

function renderIv(r) {
  let body = r.body;
  if (typeof body === "object") {
    body = r.type === "starter"
      ? `${body.title}\n• ${body.outline.join("\n• ")}`
      : JSON.stringify(body, null, 2);
  }
  const fb = r.feedback
    ? `<div class="iv-fb done">You ${r.feedback}ed this — Clutch adjusted.</div>`
    : `<div class="iv-fb" data-id="${r.id}">
        <span>Was this right?</span>
        <button class="fb accept" data-act="accept">✓ Accept</button>
        <button class="fb dismiss" data-act="dismiss">✕ Dismiss</button>
        <button class="fb snooze" data-act="snooze">⏰ Snooze</button>
      </div>`;
  return `<div class="iv"><div class="iv-head"><span class="iv-icon">${r.icon}</span>
      <div><div class="iv-title">${esc(r.title)}</div><div class="iv-task">${esc(r.task)}</div></div></div>
      <div class="iv-body">${esc(body)}</div>${fb}</div>`;
}

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ---------- data ----------
async function refresh() { renderAll(await api("/api/state")); }
function renderAll(s) { renderBoard(s.tasks); renderPanels(s); }

async function health() {
  const h = await api("/api/health");
  const m = $("#pill-model"); m.textContent = h.model; m.classList.add("ok");
  const k = $("#pill-key");
  k.textContent = h.key_present ? "API key set" : "no API key";
  k.classList.add(h.key_present ? "ok" : "warn");
}

// ---------- sweep (SSE) ----------
let sweeping = false;
function runSweep() {
  if (sweeping) return;
  sweeping = true;
  const btn = $("#btn-sweep");
  btn.disabled = true; btn.querySelector(".bolt").textContent = "⚡";
  const trace = $("#trace"); trace.innerHTML = ""; $("#summary").hidden = true;

  const es = new EventSource("/api/sweep");
  let lastStage = null;

  es.addEventListener("stage", (e) => {
    const d = JSON.parse(e.data);
    if (lastStage) lastStage.classList.remove("active");
    const el = add("stage active", `<div class="dot">${AGENT_ICON[d.agent] || "•"}</div>
      <div><div class="s-label">${d.label}</div><div class="s-desc">${esc(d.desc)}</div></div>`);
    lastStage = el;
  });
  es.addEventListener("tool", (e) => {
    const d = JSON.parse(e.data);
    add("tool", `called <code>${d.name}()</code><span class="res">${esc(summarize(d.name, d.result))}</span>`);
  });
  es.addEventListener("verdict", (e) => {
    const d = JSON.parse(e.data);
    add(`verdict ${d.at_risk ? "" : "safe"}`,
      `<span class="v-label">${d.at_risk ? "⚠ Most at-risk" : "✓ All clear"}</span>${esc(d.message)}`);
  });
  es.addEventListener("note", (e) => add("note", esc(JSON.parse(e.data).message)));
  es.addEventListener("done", (e) => {
    const d = JSON.parse(e.data);
    if (lastStage) lastStage.classList.remove("active");
    if (d.summary) { const s = $("#summary"); s.hidden = false; s.innerHTML = `✅ <b>Done.</b> ${esc(d.summary)}`; }
    renderAll(d.state);
    es.close(); finish();
  });
  es.onerror = () => { es.close(); finish(); };

  function add(cls, html) {
    const li = document.createElement("li"); li.className = cls; li.innerHTML = html;
    trace.appendChild(li); li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return li;
  }
  function finish() { sweeping = false; btn.disabled = false; }
}

function summarize(name, res) {
  if (name === "assess_risk")
    return res.most_at_risk ? `ranked ${res.ranked.length} tasks · flagged ${res.most_at_risk}` : "no task above risk threshold";
  if (name === "get_calendar_availability") return `free slots scanned`;
  if (name === "create_time_block") return `${res.summary} @ ${clockTime(res.start)}`;
  if (name === "draft_email") return "extension email drafted";
  if (name === "generate_starter") return "first-draft outline produced";
  if (name === "send_nudge") return `${res.body?.level || ""} reminder fired`;
  return "ok";
}

// ---------- eval modal ----------
async function openEval() {
  $("#eval-modal").hidden = false;
  $("#eval-score").textContent = "Running…";
  $("#eval-results").innerHTML = "";
  const e = await api("/api/eval");
  $("#eval-score").innerHTML = `<b>${e.passed}/${e.total}</b> scenarios fully correct · overall <b>${e.score.toFixed(2)}</b>`;
  const m = e.metrics || {};
  $("#eval-metrics").innerHTML = [
    ["Tool trajectory", m.trajectory], ["Decision (right task)", m.decision], ["Action (right move)", m.action],
  ].map(([k, v]) => `<div class="metric"><span class="m-k">${k}</span><span class="m-v">${(v ?? 0).toFixed(2)}</span>
      <div class="m-bar"><i style="width:${(v ?? 0) * 100}%"></i></div></div>`).join("");
  $("#eval-results").innerHTML = e.results.map((r) => `
    <div class="ev-case ${r.passed ? "pass" : "fail"}">
      <div class="ev-name">${esc(r.name)} <span class="ev-tag ${r.passed ? "pass" : "fail"}">${r.passed ? "pass" : "fail"}</span></div>
      <div class="ev-given">${esc(r.given)}</div>
      <div class="ev-traj">
        <div class="row"><span class="k">expected</span><span class="v">${r.expected.join(" → ")}</span></div>
        <div class="row"><span class="k">actual</span><span class="v">${r.got.join(" → ")}</span></div>
        <div class="row"><span class="k">decision</span><span class="v ${r.decision_ok ? "ok" : "no"}">${esc(r.decision_got ?? "no action")}${r.decision_ok ? " ✓" : " ✕"}</span></div>
        <div class="row"><span class="k">action</span><span class="v ${r.action_ok ? "ok" : "no"}">${esc(r.action_got ?? "none")}${r.action_ok ? " ✓" : " ✕"}</span></div>
      </div>
    </div>`).join("");
}

// ---------- wire up ----------
function init() {
  $("#examples").innerHTML = EXAMPLES.map((x) => `<button class="ex">${x}</button>`).join("");
  $("#examples").addEventListener("click", (e) => {
    if (!e.target.classList.contains("ex")) return;
    const t = $("#dump"); t.value = t.value ? `${t.value.trim()}, ${e.target.textContent}` : e.target.textContent;
    t.focus();
  });
  let mode = "dump";
  const DOC_SAMPLE = "Course syllabus:\n- Midterm exam on Oct 14\n- Project proposal due Nov 21\n- Final paper deadline Dec 3";
  $(".mode-tabs").addEventListener("click", (e) => {
    if (!e.target.classList.contains("mode")) return;
    mode = e.target.dataset.mode;
    document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b === e.target));
    const t = $("#dump"), doc = mode === "doc";
    $("#composer-hint").textContent = doc ? "paste prose — Clutch extracts dated items" : "comma or newline separated";
    t.placeholder = doc ? DOC_SAMPLE : "submit DBMS assignment Tue, prep DSA interview Thursday 6pm, pay hostel fee…";
    t.rows = doc ? 6 : 3;
    $("#btn-capture").textContent = doc ? "Extract tasks →" : "Capture →";
    $("#examples").style.display = doc ? "none" : "";
    $("#uploader").hidden = !doc;
    $("#upload-status").textContent = "";
  });
  $("#btn-upload").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const st = $("#upload-status");
    st.textContent = `Reading ${f.name}…`; st.className = "upload-status busy";
    const fd = new FormData(); fd.append("file", f);
    let r;
    try { r = await fetch("/api/ingest/image", { method: "POST", body: fd }).then((x) => x.json()); }
    catch { st.textContent = "upload failed"; st.className = "upload-status err"; return; }
    e.target.value = "";
    if (r.error || !r.created.length) {
      st.textContent = r.error || "no dated items found"; st.className = "upload-status err";
      return;
    }
    st.textContent = `✓ extracted ${r.created.length} task(s)`; st.className = "upload-status ok";
    renderAll(r.state);
  });
  $("#btn-capture").addEventListener("click", async () => {
    const text = $("#dump").value.trim(); if (!text) return;
    const btn = $("#btn-capture"); btn.disabled = true;
    const url = mode === "doc" ? "/api/ingest/document" : "/api/ingest";
    const r = await api(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    btn.disabled = false;
    if (mode === "doc" && !r.created.length) {
      $("#composer-hint").textContent = "no dated action items found — add dates like “Oct 14”";
      return;
    }
    $("#dump").value = ""; renderAll(r.state);
  });
  $("#interventions").addEventListener("click", async (e) => {
    const btn = e.target.closest(".fb"); if (!btn) return;
    const id = btn.closest(".iv-fb").dataset.id;
    btn.closest(".iv-fb").querySelectorAll(".fb").forEach((b) => (b.disabled = true));
    const r = await api("/api/feedback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intervention_id: id, action: btn.dataset.act }),
    });
    if (r.state) renderAll(r.state);
  });
  $("#dump").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#btn-capture").click();
  });
  $("#btn-seed").addEventListener("click", async () => renderAll(await api("/api/seed", { method: "POST" })));
  $("#btn-reset").addEventListener("click", async () => {
    renderAll(await api("/api/reset", { method: "POST" }));
    $("#trace").innerHTML = `<li class="trace-empty">Run a sweep to watch Clutch triage your tasks and intervene, live.</li>`;
    $("#summary").hidden = true;
  });
  $("#btn-sweep").addEventListener("click", runSweep);
  $("#btn-eval").addEventListener("click", openEval);
  $("#btn-eval-close").addEventListener("click", () => ($("#eval-modal").hidden = true));
  $("#eval-modal").addEventListener("click", (e) => { if (e.target.id === "eval-modal") $("#eval-modal").hidden = true; });

  health(); refresh();
}
init();
