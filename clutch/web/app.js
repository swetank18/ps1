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
    return `<div class="task ${atRisk ? "at-risk" : ""}" style="--accent:${accent[band]}; animation-delay:${i * 0.04}s">
      <div class="task-top">
        <div class="task-title">${esc(t.title)}</div>
        <span class="badge ${band}">${band}</span>
      </div>
      <div class="task-meta">
        <span>⏳ <b>${relTime(t.deadline)}</b></span>
        <span>⚙️ <b>${t.effort_minutes ?? "—"} min</b></span>
      </div>
      <div class="gauge"><i style="width:${pct}%"></i></div>
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
}

function renderIv(r) {
  let body = r.body;
  if (typeof body === "object") {
    body = r.type === "starter"
      ? `${body.title}\n• ${body.outline.join("\n• ")}`
      : JSON.stringify(body, null, 2);
  }
  return `<div class="iv"><div class="iv-head"><span class="iv-icon">${r.icon}</span>
      <div><div class="iv-title">${esc(r.title)}</div><div class="iv-task">${esc(r.task)}</div></div></div>
      <div class="iv-body">${esc(body)}</div></div>`;
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
  $("#eval-score").innerHTML = `<b>${e.passed}/${e.total}</b> trajectories correct · score <b>${e.score.toFixed(2)}</b>`;
  $("#eval-results").innerHTML = e.results.map((r) => `
    <div class="ev-case ${r.passed ? "pass" : "fail"}">
      <div class="ev-name">${esc(r.name)} <span class="ev-tag ${r.passed ? "pass" : "fail"}">${r.passed ? "pass" : "fail"}</span></div>
      <div class="ev-given">${esc(r.given)}</div>
      <div class="ev-traj">
        <div class="row"><span class="k">expected</span><span class="v">${r.expected.join(" → ")}</span></div>
        <div class="row"><span class="k">actual</span><span class="v">${r.got.join(" → ")}</span></div>
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
  $("#btn-capture").addEventListener("click", async () => {
    const text = $("#dump").value.trim(); if (!text) return;
    const r = await api("/api/ingest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    $("#dump").value = ""; renderAll(r.state);
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
