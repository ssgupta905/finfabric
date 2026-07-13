// FinFabric console. Hash-based routing, template-based views, per-view
// controllers, a persistent floating assistant, and an onboarding modal.

const $  = (id) => document.getElementById(id);
const $$ = (sel, root = document) => root.querySelectorAll(sel);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};

async function api(path, body) {
  const opts = body ? { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${path}: ${r.status}`;
    try { const j = await r.json(); if (j.detail) msg += ` — ${j.detail}`; } catch(_) {}
    throw new Error(msg);
  }
  return r.json();
}

const state = {
  health: null,
  fields: [],
  epochs: [],
  scenarios: [],
  currentScenario: null,
  chatHistory: [],
  currentView: "overview",
};

// ---------------- boot ----------------
async function boot() {
  const h = await api("/api/health");
  state.health = h;
  state.fields = h.fields;
  $("chain-mode").textContent = h.mode === "live" ? "chain · live" : "chain · fixture";
  $("chain-mode").classList.toggle("live", h.mode === "live");
  // LLM pill — show whichever provider is primary
  const llm = h.llm || {};
  let llmLabel = "LLM · off";
  if (llm.primary === "openai" && llm.openai_enabled)   llmLabel = "OpenAI · " + (llm.openai_model || "").replace("gpt-", "");
  else if (llm.primary === "gemini" && llm.gemini_enabled) llmLabel = "Gemini · " + (llm.gemini_model || "").replace("gemini-", "");
  else if (llm.openai_enabled)                          llmLabel = "OpenAI · " + (llm.openai_model || "").replace("gpt-", "");
  else if (llm.gemini_enabled)                          llmLabel = "Gemini · " + (llm.gemini_model || "").replace("gemini-", "");
  $("gemini-mode").textContent = llmLabel;
  $("gemini-mode").classList.toggle("enabled", llm.openai_enabled || llm.gemini_enabled);

  window.addEventListener("hashchange", route);
  $("refresh").addEventListener("click", () => route());
  $("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDrawer(); closeAssistant(); closeIntro(); }
    if ((e.metaKey || e.ctrlKey) && e.key === "/") { e.preventDefault(); toggleAssistant(); }
  });

  wireAssistant();
  wireIntro();

  // First-run onboarding
  if (!localStorage.getItem("finfabric.intro.seen")) openIntro();

  if (!location.hash) location.hash = "#/overview";
  route();
}

// ---------------- router ----------------
async function route() {
  const path = location.hash.replace(/^#\//, "") || "overview";
  const [name] = path.split("?");
  const known = ["overview", "scenarios", "studio", "extraction", "credentials", "reviews", "adjudicator", "audit"];
  const view = known.includes(name) ? name : "overview";
  state.currentView = view;

  $$(".side-link").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  const labels = { overview: "Overview", scenarios: "Scenarios", studio: "Studio",
                   extraction: "Extraction", credentials: "Credentials", reviews: "Review queue",
                   adjudicator: "Adjudicator", audit: "Audit" };
  $("crumbs").textContent = labels[view];

  const tpl = document.getElementById(`tpl-${view}`);
  const root = $("view-root");
  root.innerHTML = "";
  root.appendChild(tpl.content.cloneNode(true));

  updateAssistantContext();

  const controller = controllers[view];
  if (controller) await controller();
}

// ---------------- helpers ----------------
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function fmtPct(x)  { return (x * 100).toFixed(2) + "%"; }
function fmtPct4(x) { return (x * 100).toFixed(4) + "%"; }
function shortHex(s) { if (!s) return "—"; return s.length > 20 ? s.slice(0, 10) + "…" + s.slice(-8) : s; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function openDrawer(title) { $("drawer-title").textContent = title; $("drawer").classList.add("open"); $("drawer").setAttribute("aria-hidden", "false"); }
function closeDrawer() { $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); }
function debounce(fn, wait) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), wait); }; }

// ============================================================
// OVERVIEW
// ============================================================
const controllers = {};

controllers.overview = async () => {
  const d = await api("/api/dashboard");
  state.epochs = d.epochs;
  const t = d.totals;
  $("view-root").querySelector('[data-k=epochs]').textContent = t.epochs;
  $("view-root").querySelector('[data-k=credentials]').textContent = t.credentials.toLocaleString();
  $("view-root").querySelector('[data-k=escape_rate]').textContent = fmtPct4(t.escape_rate);
  $("view-root").querySelector('[data-k=cost]').textContent = "$" + (t.cost_usd || 0).toFixed(4);

  $("banner-go")?.addEventListener("click", () => location.hash = "#/scenarios");

  const tbody = $("epochs-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const e of d.epochs) {
    const tr = el("tr");
    tr.innerHTML = `
      <td class="mono">${e.epoch_id}</td>
      <td>${fmtDate(e.anchored_at)}</td>
      <td>${e.credential_count}</td>
      <td>${e.reviewed_count} <span class="muted">of ${e.docs_total}</span></td>
      <td><span class="tag ${e.escape_rate === 0 ? "good" : "bad"}">${fmtPct4(e.escape_rate)}</span></td>
      <td>${e.revoked_count ? `<span class="tag warn">${e.revoked_count}</span>` : "0"}</td>
      <td class="mono">${(e.gas_used || 0).toLocaleString()}</td>
      <td>$${(e.cost_usd || 0).toFixed(5)}</td>
      <td><a href="${e.basescan_url}" target="_blank" class="mono" onclick="event.stopPropagation()">${shortHex(e.tx_hash)}</a></td>`;
    tr.addEventListener("click", () => { location.hash = `#/credentials?epoch=${e.epoch_id}`; });
    tbody.appendChild(tr);
  }

  const tl = $("timeline");
  tl.innerHTML = "";
  for (const tx of d.chain_txs) {
    const li = el("li", tx.kind);
    const isAnchor = tx.kind === "anchor";
    li.innerHTML = `
      <div class="tl-time">${fmtTime(tx.anchored_at || Date.now()/1000)}</div>
      <div class="tl-body">
        <b>${isAnchor ? "Epoch anchored" : "Status list published"}</b><br/>
        ${isAnchor ? `epoch <b>${tx.epoch_id}</b>, ${tx.credential_count} creds, ${tx.gas_used.toLocaleString()} gas`
                    : `v${tx.version}, ${tx.gas_used || 0} gas`}
        <br/>
        <a href="${tx.basescan_url}" target="_blank">${shortHex(tx.tx_hash)}</a>
      </div>`;
    tl.appendChild(li);
  }
};

// ============================================================
// SCENARIOS
// ============================================================
let currentStream = null;

controllers.scenarios = async () => {
  const scens = await api("/api/scenarios");
  state.scenarios = scens;
  const grid = $("scen-grid");
  grid.innerHTML = "";
  for (const s of scens) {
    const card = el("div", "scen-card");
    card.dataset.key = s.key;
    card.innerHTML = `
      <div class="scen-card-title">${escapeHtml(s.name)}</div>
      <div class="scen-card-desc">${escapeHtml(s.desc)}</div>
      <div class="scen-card-tags">
        <span class="scen-card-tag">addr CER ${(s.params.address_cer * 100).toFixed(1)}%</span>
        <span class="scen-card-tag">MRZ ${(s.params.mrz_readable_rate * 100).toFixed(0)}%</span>
        ${s.params.date_swap_rate > 0.05 ? `<span class="scen-card-tag">swap ${(s.params.date_swap_rate*100).toFixed(0)}%</span>` : ""}
        ${s.params.engine_b_correlation > 0.5 ? `<span class="scen-card-tag">corr ${(s.params.engine_b_correlation*100).toFixed(0)}%</span>` : ""}
        <span class="scen-card-tag">n=${s.suggested_n}</span>
      </div>`;
    card.addEventListener("click", () => selectScenario(s));
    grid.appendChild(card);
  }

  $("scen-run").addEventListener("click", runScenario);
  $("scen-stop").addEventListener("click", stopRun);

  // Auto-select baseline and pre-render the pipeline diagram
  selectScenario(scens[0]);
  $("run-progress-card").hidden = false;
  drawPipelineGraph(null);
};

function selectScenario(s) {
  state.currentScenario = s;
  $$(".scen-card").forEach(c => c.classList.toggle("selected", c.dataset.key === s.key));
  $("scen-selected").textContent = s.name;
  $("scen-selected-sub").textContent = s.desc;
  $("scen-desc").textContent = "Adjust any knob below, then Run scenario. Decisions stream in real time; accepted credentials anchor into a new epoch.";
  $("scen-form").hidden = false;
  $("scen-expect").innerHTML = `<b>Predicted:</b> ${escapeHtml(s.expected)}`;

  // Preload form values
  $("scen-n").value = s.suggested_n;
  $("scen-cer").value = s.params.cer_multiplier;
  $("scen-addr").value = s.params.address_cer;
  $("scen-mrz").value = s.params.mrz_readable_rate;
  $("scen-swap").value = s.params.date_swap_rate;
  $("scen-corr").value = s.params.engine_b_correlation;
}

function stopRun() {
  if (currentStream) { currentStream.close(); currentStream = null; }
  $("scen-run").disabled = false;
  $("scen-stop").disabled = true;
  $("run-progress-txt").textContent = "stopped";
}

// Pipeline stages. Order matches the actual pipeline execution.
const PIPELINE_STAGES = [
  { key: "capture",    label: "Capture",    sub: "KYC document",     glyph: "◈" },
  { key: "extract",    label: "Extract",    sub: "OCR × 2 + MRZ",    glyph: "▤" },
  { key: "gate",       label: "Gate",       sub: "5 signals / field", glyph: "◉" },
  { key: "adjudicate", label: "Adjudicate", sub: "VLM 3rd opinion",  glyph: "◇" },
  { key: "commit",     label: "Commit",     sub: "Merkle leaves",    glyph: "▦" },
  { key: "anchor",     label: "Anchor",     sub: "Base tx",          glyph: "⛓" },
];

function drawPipelineGraph(activeStage = null, hotStage = null, stageStates = {}) {
  const svg = $("pipeline-svg");
  if (!svg) return;
  const W = 900, H = 130;
  const n = PIPELINE_STAGES.length;
  const nodeR = 22, nodeGap = (W - 60) / (n - 1);

  // arrow marker (once)
  let defs = '<defs><marker id="edge-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#c7c7cc"/></marker></defs>';
  let out = defs;
  // edges
  for (let i = 0; i < n - 1; i++) {
    const x1 = 30 + i * nodeGap + nodeR;
    const x2 = 30 + (i + 1) * nodeGap - nodeR;
    const isActive = activeStage === PIPELINE_STAGES[i + 1].key;
    out += `<line class="edge ${isActive ? "pulse" : ""}" x1="${x1}" y1="${H/2}" x2="${x2}" y2="${H/2}"/>`;
  }
  // nodes
  for (let i = 0; i < n; i++) {
    const s = PIPELINE_STAGES[i];
    const cx = 30 + i * nodeGap;
    const cy = H / 2;
    const cls = ["stage-node"];
    if (s.key === activeStage) cls.push("hot");
    else if (stageStates[s.key] === "active") cls.push("active");
    else if (stageStates[s.key] === "warn") cls.push("warn");
    else if (stageStates[s.key] === "bad") cls.push("bad");
    out += `<circle class="${cls.join(" ")}" cx="${cx}" cy="${cy}" r="${nodeR}"/>`;
    out += `<text class="stage-glyph" x="${cx}" y="${cy}">${s.glyph}</text>`;
    out += `<text class="stage-lbl" x="${cx}" y="${cy + nodeR + 14}">${s.label}</text>`;
    out += `<text class="stage-sub" x="${cx}" y="${cy + nodeR + 26}">${s.sub}</text>`;
  }
  svg.innerHTML = out;
}

// Chain-of-thought helpers
function cotLine(cls, html) {
  const stream = $("cot-stream");
  const line = el("div", "cot-line " + cls);
  line.innerHTML = html;
  stream.appendChild(line);
  stream.scrollTop = stream.scrollHeight;
  // keep last 400 lines
  while (stream.children.length > 400) stream.removeChild(stream.firstChild);
}

function renderDocInspect(docEvt) {
  const inspect = $("doc-inspect");
  const truth = docEvt.truth || {};
  const decisions = docEvt.decisions || [];
  const custName = truth.name || decisions.find(d => d.field === "name")?.truth || "—";
  const rows = [];
  rows.push(`<div class="doc-title">${escapeHtml(custName)} · <span class="mono">${docEvt.doc_id}</span></div>`);
  rows.push(`<div class="doc-sub">${escapeHtml(docEvt.subject_did || "")}</div>`);
  for (const dec of decisions) {
    const truthVal = dec.truth ?? "";
    const ocrA = dec.ocr_a ?? "";
    const ocrDiffers = ocrA !== truthVal;
    let tag;
    if (dec.accepted && dec.correct) tag = '<span class="tag good">issue</span>';
    else if (dec.accepted && !dec.correct) tag = '<span class="tag bad">ESCAPE</span>';
    else tag = '<span class="tag warn">review</span>';
    const mrzLine = dec.mrz_value ? `<div class="mrz">MRZ: ${escapeHtml(dec.mrz_value)}</div>` : "";
    rows.push(`
      <div class="field-row">
        <div class="fr-label">${dec.label}</div>
        <div class="fr-mid">
          <div class="truth">truth: ${escapeHtml(truthVal)}</div>
          <div class="ocr ${ocrDiffers ? "differs" : ""}">A: ${escapeHtml(ocrA)} <span class="muted">(${(dec.confidence * 100).toFixed(0)}%)</span></div>
          <div class="ocr ${dec.ocr_b !== truthVal ? "differs" : ""}">B: ${escapeHtml(dec.ocr_b || "")}</div>
          ${mrzLine}
        </div>
        <div class="fr-tag">${tag}${!dec.accepted ? `<div class="muted" style="font-size:10px; margin-top:4px; text-align:right">${escapeHtml(dec.reason || "")}</div>` : ""}</div>
      </div>`);
  }
  inspect.innerHTML = rows.join("");
}

function stageNarrative(stage, doc_id, detail) {
  const stageLabel = { capture: "CAPTURE", extract: "EXTRACT", gate: "GATE",
                       adjudicate: "ADJUDICATE", commit: "COMMIT", anchor: "ANCHOR" }[stage] || stage.toUpperCase();
  cotLine("stage", `<span class="stage-name">${stageLabel}</span> ${escapeHtml(detail)} <span class="muted">· ${doc_id}</span>`);
}

async function runScenario() {
  if (!state.currentScenario) return;
  const s = state.currentScenario;
  const n = +$("scen-n").value || s.suggested_n;
  const seed = $("scen-seed").value;
  const anchor = $("scen-anchor").checked;
  const pace = $("scen-pace").value;
  const params = new URLSearchParams({
    n, anchor, scenario: s.key,
    cer_multiplier: $("scen-cer").value,
    address_cer: $("scen-addr").value,
    mrz_readable_rate: $("scen-mrz").value,
    date_swap_rate: $("scen-swap").value,
    engine_b_correlation: $("scen-corr").value,
    pace_ms: pace,
  });
  if (seed) params.set("seed", seed);

  // Reset UI
  $("run-progress-card").hidden = false;
  $("live-table-card").hidden = false;
  $("run-anchor-card").hidden = true;
  $("live-table").querySelector("tbody").innerHTML = "";
  ["c-accept","c-review","c-recap","c-esc","c-raw"].forEach(id => $(id).textContent = id === "c-esc" ? "0.0000%" : id === "c-raw" ? "—" : "0");
  $("run-bar").style.width = "0%";
  $("cot-stream").innerHTML = "";
  $("doc-inspect").innerHTML = '<em class="muted">Waiting for first document…</em>';
  drawPipelineGraph(null);
  cotLine("doc-head", `▶ Starting <b>${escapeHtml(s.name)}</b> · n=${n} · pace ${pace}ms/stage`);

  const es = new EventSource(`/api/issue/stream?${params}`);
  currentStream = es;
  $("scen-run").disabled = true;
  $("scen-stop").disabled = false;
  $("run-progress-txt").textContent = "streaming…";

  let currentDocId = null;
  const tbody = $("live-table").querySelector("tbody");
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);

    if (e.type === "stage") {
      if (e.doc_id !== currentDocId) {
        currentDocId = e.doc_id;
        cotLine("doc-head", `📄 Document ${e.index + 1} of ${e.total} — <span class="mono">${e.doc_id}</span>`);
        $("cot-doc").textContent = e.doc_id;
      }
      drawPipelineGraph(e.stage);
      stageNarrative(e.stage, e.doc_id, e.detail);
      $("run-progress-txt").textContent = `${e.index + 1}/${e.total} · ${e.stage} · ${e.doc_id}`;

    } else if (e.type === "doc") {
      $("run-bar").style.width = ((e.index + 1) / e.total * 100) + "%";
      $("run-progress-txt").textContent = `${e.index + 1}/${e.total}  ·  ${e.doc_id}  ·  ${e.action}`;

      // Update counters
      const rs = e.running_stats;
      $("c-accept").textContent = rs.auto_accepted;
      $("c-review").textContent = rs.reviewed;
      $("c-recap").textContent = rs.recapture;
      $("c-raw").textContent = rs.field_total ? fmtPct(rs.field_ok_raw / rs.field_total) : "—";
      $("c-esc").textContent = rs.field_accepted ? fmtPct4(rs.escapes / (rs.field_accepted + rs.field_reviewed)) : "0.0000%";

      // Update doc inspector
      renderDocInspect(e);
      const custName = (e.truth || {}).name || "customer";
      $("doc-panel-status").textContent = `${custName}  ·  ${e.action}  ·  ${e.latency_ms}ms`;

      // CoT: decision summary
      const actionCls = e.action === "issue" ? "decision-ok" : e.action === "recapture" ? "decision-bad" : "";
      const custShort = escapeHtml(((e.truth || {}).name || "").slice(0, 24));
      if (e.action === "issue") {
        cotLine(actionCls, `✓ AUTO-ISSUED · ${custShort} · 10/10 fields cleared the gate · epoch tx pending`);
      } else if (e.action === "recapture") {
        cotLine(actionCls, `✗ RECAPTURE · ${custShort} · MRZ unreadable — customer will be asked to retake the photo`);
      } else {
        cotLine(actionCls, `⚠ TO REVIEWER · ${custShort} · ${e.reviewed_fields.length} field${e.reviewed_fields.length !== 1 ? "s" : ""} flagged: ${escapeHtml(e.reviewed_fields.join(", "))}`);
      }

      // Update pipeline graph — mark stages complete based on action
      const stageStates = {};
      ["capture","extract","gate"].forEach(k => stageStates[k] = "active");
      if (e.reviewed_fields.length) stageStates["adjudicate"] = "active";
      if (e.action === "issue") { stageStates["commit"] = "active"; stageStates["anchor"] = "active"; }
      drawPipelineGraph(null, null, stageStates);

      // Append to live table
      const tr = el("tr");
      const actionTag = e.action === "issue" ? '<span class="tag good">issue</span>'
                      : e.action === "review" ? '<span class="tag warn">review</span>'
                      : '<span class="tag bad">recapture</span>';
      tr.innerHTML = `
        <td class="mono">${e.index + 1}</td>
        <td class="mono">${e.doc_id}</td>
        <td>${custShort}</td>
        <td>${actionTag}</td>
        <td class="mono">${e.reviewed_fields.slice(0, 3).join(", ") || "—"}</td>
        <td class="mono">${e.latency_ms}ms</td>`;
      // click a row → re-render the inspector with THAT event
      tr._evt = e;
      tr.addEventListener("click", () => renderDocInspect(tr._evt));
      tbody.insertBefore(tr, tbody.firstChild);
      while (tbody.rows.length > 200) tbody.deleteRow(-1);

    } else if (e.type === "anchor") {
      const r = e.receipt;
      drawPipelineGraph("anchor");
      cotLine("stage", `<span class="stage-name">ANCHOR</span> epoch ${e.epoch_id} · ${r.credential_count} credentials in one Base tx · ${r.gas_used.toLocaleString()} gas · $${(r.cost_usd || 0).toFixed(6)}`);
      $("run-anchor-card").hidden = false;
      $("run-anchor-receipt").innerHTML = `
        <div class="k">Epoch ID</div><div class="v mono">${e.epoch_id}</div>
        <div class="k">Epoch root</div><div class="v mono">${r.epoch_root_hex}</div>
        <div class="k">Credentials</div><div class="v">${r.credential_count} in one tx</div>
        <div class="k">Gas used</div><div class="v mono">${r.gas_used.toLocaleString()}</div>
        <div class="k">Cost (est.)</div><div class="v">$${(r.cost_usd || 0).toFixed(6)}</div>
        <div class="k">Mode</div><div class="v"><span class="tag ${r.mode === "live" ? "good" : "info"}">${r.mode}</span></div>
        <div class="k">Basescan</div><div class="v"><a href="${r.basescan_url}" target="_blank" class="mono">${shortHex(r.tx_hash)}</a></div>`;

    } else if (e.type === "done") {
      $("run-progress-txt").textContent = `done · ${e.stats.documents} docs · ${e.stats.escapes} escapes`;
      cotLine("doc-head", `■ Run complete — ${e.stats.documents} docs · ${e.stats.auto_accepted} auto-issued · ${e.stats.reviewed} to reviewer · ${e.stats.recapture} recapture · ${e.stats.escapes} escapes`);
      drawPipelineGraph(null);
      es.close(); currentStream = null;
      $("scen-run").disabled = false; $("scen-stop").disabled = true;
    } else if (e.type === "error") {
      $("run-progress-txt").textContent = "error: " + e.message;
      cotLine("decision-bad", "ERROR · " + escapeHtml(e.message));
      es.close(); currentStream = null;
      $("scen-run").disabled = false; $("scen-stop").disabled = true;
    }
  };
  es.onerror = () => {
    $("run-progress-txt").textContent = "stream error";
    es.close(); currentStream = null;
    $("scen-run").disabled = false; $("scen-stop").disabled = true;
  };
}

// ============================================================
// EXTRACTION — VLM + dual-OCR document analyser
// ============================================================
controllers.extraction = async () => {
  // Wait for the image to load so we know the natural aspect
  const img = $("ex-img");
  if (!img.complete) await new Promise(r => (img.onload = r));

  $("ex-run").addEventListener("click", runExtraction);
  // Auto-run once on first visit so the view isn't empty
  await runExtraction();
};

async function runExtraction() {
  const btn = $("ex-run");
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Analysing';
  $("ex-status").textContent = "step 1 · document classification…";
  const svg = $("ex-overlay");
  svg.innerHTML = "";
  $("ex-vlm-val").textContent = "…";
  ["ex-issued", "ex-repaired", "ex-review", "ex-escapes"].forEach(id => $(id).textContent = "—");
  $("ex-fields").innerHTML = '<em class="muted">Running…</em>';

  const seed = Math.floor(Math.random() * 100000);
  const r = await api(`/api/document/analyze?seed=${seed}`);

  // Use the schema box coordinate space as the SVG viewBox — pixel-perfect
  // overlay regardless of the image's rendered size.
  svg.setAttribute("viewBox", `0 0 ${r.width} ${r.height}`);

  // Stage 1: VLM classification
  await sleep(500);
  $("ex-vlm-val").textContent = `${r.vlm.class}  ·  ${(r.vlm.confidence * 100).toFixed(1)}% conf`;
  $("ex-status").textContent = "step 2 · layout detection…";

  // Stage 2: draw all bounding boxes as skeletons (grey, opacity 0)
  const nsSvg = "http://www.w3.org/2000/svg";
  const boxEls = {};
  for (const f of r.fields) {
    const b = f.box;
    const rect = document.createElementNS(nsSvg, "rect");
    rect.setAttribute("x", b.x); rect.setAttribute("y", b.y);
    rect.setAttribute("width", b.w); rect.setAttribute("height", b.h);
    rect.setAttribute("rx", 3);
    rect.setAttribute("class", `box status-${f.status}`);
    svg.appendChild(rect);
    const fill = document.createElementNS(nsSvg, "rect");
    fill.setAttribute("x", b.x); fill.setAttribute("y", b.y);
    fill.setAttribute("width", b.w); fill.setAttribute("height", b.h);
    fill.setAttribute("rx", 3);
    fill.setAttribute("class", `box box-fill status-${f.status}`);
    fill.style.opacity = "0";
    svg.appendChild(fill);
    const lbl = document.createElementNS(nsSvg, "text");
    lbl.setAttribute("x", b.x + 4);
    lbl.setAttribute("y", b.y - 4);
    lbl.setAttribute("class", "box-lbl");
    lbl.textContent = f.label;
    svg.appendChild(lbl);
    boxEls[f.name] = { rect, fill, lbl };
  }
  // MRZ zone
  const mz = document.createElementNS(nsSvg, "rect");
  mz.setAttribute("x", r.mrz_box.x); mz.setAttribute("y", r.mrz_box.y);
  mz.setAttribute("width", r.mrz_box.w); mz.setAttribute("height", r.mrz_box.h);
  mz.setAttribute("rx", 4);
  mz.setAttribute("class", "mrz-zone");
  svg.appendChild(mz);

  await sleep(400);

  // Reveal boxes one by one (top-to-bottom, left-to-right by y then x)
  const ordered = [...r.fields].sort((a, b) => a.box.y - b.box.y || a.box.x - b.box.x);
  for (const f of ordered) {
    boxEls[f.name].rect.classList.add("show");
    boxEls[f.name].lbl.classList.add("show");
    await sleep(90);
  }
  $("ex-status").textContent = "step 3 · dual-OCR + MRZ verify…";

  // Fill boxes with status color
  await sleep(400);
  for (const f of ordered) {
    boxEls[f.name].fill.style.opacity = "0.22";
    await sleep(70);
  }
  // MRZ zone reveal
  mz.classList.add("show");

  // Right-panel population
  $("ex-status").textContent = "step 4 · gate decisions…";
  $("ex-issued").textContent = r.summary.auto_issued;
  $("ex-repaired").textContent = r.summary.repaired_from_mrz;
  $("ex-review").textContent = r.summary.to_review;
  $("ex-escapes").textContent = r.summary.escapes;

  const list = $("ex-fields");
  list.innerHTML = "";
  for (const f of r.fields) {
    const item = el("div", `ex-field status-${f.status}`);
    const ocrDiffers = f.ocr_a !== f.truth;
    const mrzLine = f.mrz_value ? `<div>MRZ: <span class="repair">${escapeHtml(f.mrz_value)}</span></div>` : "";
    item.innerHTML = `
      <div class="f-label">${escapeHtml(f.label)}</div>
      <div class="f-values">
        <div>truth: ${escapeHtml(f.truth)}</div>
        <div>OCR A: <span class="${ocrDiffers ? 'diff' : ''}">${escapeHtml(f.ocr_a)}</span> <span class="muted">(${(f.ocr_a_conf * 100).toFixed(0)}%)</span></div>
        <div>OCR B: <span class="${f.ocr_b !== f.truth ? 'diff' : ''}">${escapeHtml(f.ocr_b)}</span></div>
        ${mrzLine}
      </div>
      <div class="f-status">${escapeHtml(f.status.replace("_", " "))}</div>`;
    // Hover a field → highlight its box
    item.addEventListener("mouseenter", () => {
      boxEls[f.name].rect.style.strokeWidth = "5";
    });
    item.addEventListener("mouseleave", () => {
      boxEls[f.name].rect.style.strokeWidth = "";
    });
    list.appendChild(item);
  }

  $("ex-status").textContent = `done · analysed in ${r.summary.total_fields} fields`;
  btn.disabled = false; btn.textContent = "▶ Re-analyse";
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// ============================================================
// STUDIO v2 — agentic workflow builder
// ============================================================
const studioState = { workflow: null, running: false };

async function loadPresets() {
  const wfs = await api("/api/workflows");
  const list = $("preset-list");
  list.innerHTML = "";
  for (const w of wfs) {
    const item = el("div", "preset-item");
    item.innerHTML = `
      <div>
        <div class="preset-name">${escapeHtml(w.name)}</div>
        <div class="preset-desc">${escapeHtml(w.description || "")}</div>
      </div>
      <div class="preset-meta">${w.config.nodes.length} nodes${w.run_count ? " · " + w.run_count + "×" : ""}</div>`;
    item.addEventListener("click", () => loadWorkflow(w));
    list.appendChild(item);
  }
}

async function loadCapabilitiesPalette() {
  const caps = await api("/api/capabilities");
  const p = $("cap-palette");
  p.innerHTML = "";
  for (const c of caps) {
    const it = el("div", "cap-item");
    it.dataset.cat = c.category;
    it.innerHTML = `<span class="cap-dot"></span><span>${escapeHtml(c.label)}</span>`;
    it.title = `${c.key} — inputs: ${c.inputs.join(", ") || "—"} · outputs: ${c.outputs.join(", ") || "—"}`;
    p.appendChild(it);
  }
}

function loadWorkflow(w) {
  studioState.workflow = w;
  $("wf-name").textContent = w.name || "Untitled workflow";
  $("wf-desc").textContent = w.description || "";
  $("wf-run").disabled = false;
  $("wf-save").disabled = false;
  const hasBtns = ["wf-add-btn","wf-rm-btn","wf-refine-btn","wf-upload-run"];
  hasBtns.forEach(id => { const b = $(id); if (b) b.disabled = false; });
  $$(".preset-item").forEach(el => el.classList.toggle("selected", el.querySelector(".preset-name")?.textContent === w.name));
  drawWorkflow(w.config);
  refreshEditorDropdowns();
  // Reset run state
  $("wf-record-block").hidden = true;
  $("wf-log-block").hidden = true;
  $("wf-final-block").hidden = true;
  $("wf-upload-summary").hidden = true;
  $("wf-pdf-btn").hidden = true;
}

async function refreshEditorDropdowns() {
  if (!studioState.workflow) return;
  const caps = studioState.caps || (studioState.caps = await api("/api/capabilities"));
  const addCap = $("wf-add-cap"); addCap.innerHTML = "";
  for (const c of caps) {
    const o = el("option"); o.value = c.key; o.textContent = `${c.label} [${c.category}]`;
    addCap.appendChild(o);
  }
  const addAfter = $("wf-add-after"); addAfter.innerHTML = '<option value="">(new start)</option>';
  const rmNode = $("wf-rm-node"); rmNode.innerHTML = "";
  for (const n of studioState.workflow.config.nodes) {
    const o1 = el("option"); o1.value = n.id; o1.textContent = `${n.id} · ${n.cap}`; addAfter.appendChild(o1);
    const o2 = el("option"); o2.value = n.id; o2.textContent = `${n.id} · ${n.cap}`; rmNode.appendChild(o2);
  }
  // Auto-select the topological leaf as insertion point
  if (studioState.workflow.config.nodes.length) {
    addAfter.value = studioState.workflow.config.nodes[studioState.workflow.config.nodes.length - 1].id;
  }
}

async function saveCurrentWorkflow(newConfig, newName) {
  if (!studioState.workflow) return;
  const saved = await api("/api/workflows", {
    name: newName || (studioState.workflow.name + " ·"),
    description: studioState.workflow.description || "",
    config: newConfig,
  });
  // Also delete the old one to avoid accumulating copies
  if (studioState.workflow.id && studioState.workflow.id !== saved.id) {
    await fetch(`/api/workflows/${studioState.workflow.id}`, { method: "DELETE" }).catch(() => {});
  }
  await loadPresets();
  loadWorkflow(saved);
}

function addNodeInline() {
  const w = studioState.workflow; if (!w) return;
  const cap = $("wf-add-cap").value;
  const after = $("wf-add-after").value;
  // Pick a unique id
  const base = cap.split("_")[0];
  let n = 1;
  const existingIds = new Set(w.config.nodes.map(x => x.id));
  let id = base + "_" + n;
  while (existingIds.has(id)) { n++; id = base + "_" + n; }
  const cfg = JSON.parse(JSON.stringify(w.config));
  cfg.nodes.push({ id, cap, params: {} });
  if (after) cfg.edges.push([after, id]);
  saveCurrentWorkflow(cfg, w.name);
}

function removeNodeInline() {
  const w = studioState.workflow; if (!w) return;
  const id = $("wf-rm-node").value;
  const cfg = JSON.parse(JSON.stringify(w.config));
  cfg.nodes = cfg.nodes.filter(n => n.id !== id);
  cfg.edges = cfg.edges.filter(([a, b]) => a !== id && b !== id);
  saveCurrentWorkflow(cfg, w.name);
}

async function refineInline() {
  const w = studioState.workflow; if (!w) return;
  const msg = $("wf-refine-input").value.trim();
  if (!msg) return;
  const btn = $("wf-refine-btn");
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Refining';
  try {
    const r = await api("/api/copilot/refine", { workflow: w, change_request: msg });
    if (!r.ok) { alert("Refine failed: " + (r.error || "unknown")); return; }
    $("wf-refine-input").value = "";
    // Save + reload
    const saved = await api("/api/workflows", {
      name: r.workflow.name || w.name + " (refined)",
      description: r.workflow.description || w.description,
      config: r.workflow.config,
    });
    if (w.id && w.id !== saved.id) {
      await fetch(`/api/workflows/${w.id}`, { method: "DELETE" }).catch(() => {});
    }
    await loadPresets();
    loadWorkflow(saved);
  } catch (e) {
    alert("Refine error: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "✦ Refine";
  }
}

function drawWorkflow(config, states = {}, activeId = null) {
  const svg = $("wf-svg");
  const W = 900, H = 320;
  const nodes = config.nodes || [];
  const edges = config.edges || [];

  // Layered layout — topological levels
  const level = {};
  const incoming = {};
  for (const n of nodes) { level[n.id] = 0; incoming[n.id] = 0; }
  for (const [a, b] of edges) incoming[b] = (incoming[b] || 0) + 1;
  // BFS: level = 1 + max(source levels)
  let changed = true;
  while (changed) {
    changed = false;
    for (const [a, b] of edges) {
      if (level[b] < level[a] + 1) { level[b] = level[a] + 1; changed = true; }
    }
  }
  const maxLevel = Math.max(0, ...Object.values(level));
  const perLevel = {};
  for (const n of nodes) {
    const L = level[n.id];
    if (!perLevel[L]) perLevel[L] = [];
    perLevel[L].push(n);
  }
  // Position
  const pos = {};
  const marginX = 60, marginY = 40;
  const nodeW = 130, nodeH = 46;
  const availW = W - marginX * 2, availH = H - marginY * 2;
  for (let L = 0; L <= maxLevel; L++) {
    const col = perLevel[L] || [];
    const x = maxLevel === 0 ? W / 2 : marginX + (availW * L / maxLevel);
    for (let i = 0; i < col.length; i++) {
      const y = col.length === 1 ? H / 2 : marginY + (availH * i / (col.length - 1));
      pos[col[i].id] = { x, y };
    }
  }

  let out = '<defs><marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#c7c7cc"/></marker></defs>';
  // edges first
  for (const [a, b] of edges) {
    const p1 = pos[a], p2 = pos[b];
    if (!p1 || !p2) continue;
    const midX = (p1.x + p2.x) / 2;
    const path = `M${p1.x + nodeW/2},${p1.y} C${midX},${p1.y} ${midX},${p2.y} ${p2.x - nodeW/2},${p2.y}`;
    const done = states[a] === "ok" && states[b];
    out += `<path class="wf-edge ${done ? "done" : ""}" d="${path}" />`;
  }
  // nodes
  for (const n of nodes) {
    const p = pos[n.id]; if (!p) continue;
    const cls = ["wf-node"];
    if (n.id === activeId) cls.push("active");
    else if (states[n.id] === "ok") cls.push("ok");
    else if (states[n.id] === "bad") cls.push("bad");
    out += `<rect class="${cls.join(" ")}" x="${p.x - nodeW/2}" y="${p.y - nodeH/2}" width="${nodeW}" height="${nodeH}" rx="8" ry="8"/>`;
    out += `<text class="wf-lbl" x="${p.x}" y="${p.y - 3}">${escapeHtml(n.id)}</text>`;
    out += `<text class="wf-sub" x="${p.x}" y="${p.y + 12}">${escapeHtml(n.cap)}</text>`;
  }
  svg.innerHTML = out;
}

async function runWorkflow() {
  if (!studioState.workflow) return;
  if (studioState.running) return;
  studioState.running = true;
  $("wf-run").disabled = true;
  $("wf-log-block").hidden = false;
  $("wf-final-block").hidden = true;
  $("wf-log").innerHTML = "";

  const kind = $("wf-record-kind").value;
  const url = `/api/workflow/run_stream?workflow_id=${studioState.workflow.id}&kind=${kind}&pace_ms=220`;
  const es = new EventSource(url);
  const states = {};

  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "record") {
      $("wf-record-block").hidden = false;
      const rec = e.record;
      const rows = ["name","pan","gstin","aadhaar_masked","nationality","status","address","ifsc","pincode"]
        .filter(k => rec[k])
        .map(k => `<div class="k">${k}</div><div class="v">${escapeHtml(String(rec[k]).slice(0, 60))}</div>`)
        .join("");
      $("wf-record").innerHTML = rows;
    } else if (e.type === "node_start") {
      drawWorkflow(studioState.workflow.config, states, e.node_id);
    } else if (e.type === "node_end") {
      states[e.node_id] = e.ok ? "ok" : "bad";
      drawWorkflow(studioState.workflow.config, states, null);
      const row = el("div", "wf-log-row " + (e.ok ? "ok" : "bad"));
      row.innerHTML = `
        <span class="step-cap">${escapeHtml(e.cap)}</span>
        <span class="step-detail">${escapeHtml(e.detail || "")}</span>
        <span class="step-time">${e.duration_ms ?? 0}ms</span>`;
      $("wf-log").appendChild(row);
      $("wf-log").scrollTop = $("wf-log").scrollHeight;
    } else if (e.type === "run_end") {
      if (e.anchor_receipt) {
        const r = e.anchor_receipt;
        $("wf-final-block").hidden = false;
        $("wf-final").innerHTML = `
          <div class="k">Epoch ID</div><div class="v mono">${r.epoch_id}</div>
          <div class="k">Root</div><div class="v mono">${r.epoch_root_hex}</div>
          <div class="k">Gas</div><div class="v mono">${r.gas_used.toLocaleString()}</div>
          <div class="k">Cost</div><div class="v">$${(r.cost_usd || 0).toFixed(6)}</div>
          <div class="k">Tx</div><div class="v"><a class="mono" target="_blank" href="${r.basescan_url}">${shortHex(r.tx_hash)}</a></div>`;
      } else {
        $("wf-final-block").hidden = false;
        $("wf-final").innerHTML = `<div class="k">Anchor</div><div class="v muted">skipped (upstream flagged the record)</div>`;
      }
    } else if (e.type === "final") {
      es.close();
      studioState.running = false;
      $("wf-run").disabled = false;
      // Show PDF download for this run
      if (e.run_id) {
        const btn = $("wf-pdf-btn");
        btn.hidden = false;
        btn.href = `/api/report/pdf/${e.run_id}`;
      }
    } else if (e.type === "error") {
      es.close();
      studioState.running = false;
      $("wf-run").disabled = false;
      alert("Run error: " + e.message);
    }
  };
  es.onerror = () => {
    es.close(); studioState.running = false; $("wf-run").disabled = false;
  };
}

async function copilotGenerate() {
  const desc = $("copilot-desc").value.trim();
  if (!desc) return;
  const btn = $("copilot-run");
  const out = $("copilot-out");
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Thinking';
  out.className = "copilot-out"; out.textContent = "Copilot is composing your workflow…";
  try {
    const r = await api("/api/copilot/generate", { description: desc });
    if (!r.ok) {
      out.className = "copilot-out err";
      out.textContent = "Copilot: " + (r.error || "failed");
      return;
    }
    // Save it so it appears in presets, then load it
    const saved = await api("/api/workflows", {
      name: r.workflow.name || "Copilot workflow",
      description: r.workflow.description || desc,
      config: r.workflow.config,
    });
    out.className = "copilot-out ok";
    out.textContent = `Generated "${saved.name}" with ${saved.config.nodes.length} nodes — loaded above.`;
    await loadPresets();
    loadWorkflow(saved);
  } catch (e) {
    out.className = "copilot-out err";
    out.textContent = "Error: " + e.message;
  } finally {
    btn.disabled = false; btn.textContent = "✦ Generate workflow";
  }
}

controllers.studio = async () => {
  await Promise.all([loadPresets(), loadCapabilitiesPalette()]);
  $("copilot-run").addEventListener("click", copilotGenerate);
  $$("#copilot-suggest .chip").forEach(c => c.addEventListener("click", () => {
    $("copilot-desc").value = c.dataset.msg;
    copilotGenerate();
  }));
  $("wf-run").addEventListener("click", runWorkflow);
  $("wf-save").addEventListener("click", async () => {
    if (!studioState.workflow) return;
    const name = prompt("Save as:", studioState.workflow.name + " (copy)");
    if (!name) return;
    const saved = await api("/api/workflows", {
      name, description: studioState.workflow.description,
      config: studioState.workflow.config,
    });
    await loadPresets();
    loadWorkflow(saved);
  });

  // Manual node editor
  $("wf-add-btn").addEventListener("click", addNodeInline);
  $("wf-rm-btn").addEventListener("click", removeNodeInline);
  $("wf-refine-btn").addEventListener("click", refineInline);
  $("wf-refine-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); refineInline(); }
  });

  // File upload
  const drop = $("wf-upload-drop"), fileEl = $("wf-file"), runBtn = $("wf-upload-run");
  fileEl.addEventListener("change", () => {
    if (fileEl.files[0]) {
      $("wf-upload-lbl").textContent = fileEl.files[0].name + " · ready";
      drop.classList.add("has-file");
    }
  });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("dragover"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); drop.classList.remove("dragover");
    if (e.dataTransfer.files[0]) {
      fileEl.files = e.dataTransfer.files;
      fileEl.dispatchEvent(new Event("change"));
    }
  });
  runBtn.addEventListener("click", uploadAndRun);

  // Auto-select first preset
  const wfs = await api("/api/workflows");
  if (wfs.length) loadWorkflow(wfs[wfs.length - 1]);
};

async function uploadAndRun() {
  const w = studioState.workflow; if (!w) return;
  const file = $("wf-file").files[0];
  if (!file) { alert("Choose a CSV file first (or download the sample below to see the expected format)."); return; }
  const btn = $("wf-upload-run");
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Running batch';
  try {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch(`/api/workflow/upload_run?workflow_id=${w.id}`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const summary = $("wf-upload-summary");
    summary.hidden = false;
    summary.innerHTML = `
      <b>Batch complete.</b> Workflow "${escapeHtml(w.name)}" ran on ${data.total_records} customer records.
      <div class="kpis">
        <div class="kpi-mini good"><div class="n">${data.passed}</div><div class="l">auto-passed</div></div>
        <div class="kpi-mini ${data.flagged ? 'bad' : ''}"><div class="n">${data.flagged}</div><div class="l">flagged</div></div>
        <div class="kpi-mini bad"><div class="n">${data.critical}</div><div class="l">critical</div></div>
        <div class="kpi-mini"><div class="n">${data.total_records}</div><div class="l">total</div></div>
      </div>
      <a class="btn primary" href="/api/report/pdf/${data.run_id}" download>⬇ Download compliance PDF</a>
      <div class="muted" style="margin-top:8px">Every flagged record's PDF entry cites the specific RBI / DPDP / PMLA guidance and the SOP-recommended next step.</div>`;
  } catch (e) {
    alert("Upload run failed: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Run on uploaded data";
  }
}

// Legacy studio v1 (kept for reference but no longer bound)
const _legacyStudio = async () => {
  const cerLabels = (v) => {
    if (v <= 0.15) return `bank-tuned (address CER ${(v * 7.9).toFixed(2)}%)`;
    if (v <= 0.5)  return `improved (address CER ${(v * 7.9).toFixed(2)}%)`;
    if (v <= 0.85) return `average (address CER ${(v * 7.9).toFixed(2)}%)`;
    return `baseline (${(v * 7.9).toFixed(1)}% address CER)`;
  };
  const mrzLabels = (v) => {
    if (v >= 0.9) return `studio-lit (${(v * 100).toFixed(0)}% readable)`;
    if (v >= 0.7) return `normal phone photo (${(v * 100).toFixed(0)}% readable)`;
    if (v >= 0.4) return `some glare (${(v * 100).toFixed(0)}% readable)`;
    return `heavy glare (${(v * 100).toFixed(0)}% readable)`;
  };
  const swapLabels = (v) => {
    if (v <= 0.03) return `${(v * 100).toFixed(0)}% baseline`;
    if (v <= 0.1)  return `${(v * 100).toFixed(0)}% elevated`;
    if (v <= 0.25) return `${(v * 100).toFixed(0)}% high — active attack`;
    return `${(v * 100).toFixed(0)}% — full attack pattern`;
  };
  const corrLabels = (v) => {
    if (v <= 0.2)  return `${(v * 100).toFixed(0)}% (fully independent)`;
    if (v <= 0.5)  return `${(v * 100).toFixed(0)}% (typical)`;
    return `${(v * 100).toFixed(0)}% (same glare)`;
  };
  const update = () => {
    const cer = +$("st-cer").value, mrz = +$("st-mrz").value,
          swap = +$("st-swap").value, corr = +$("st-corr").value;
    $("st-cer-val").textContent  = cerLabels(cer);
    $("st-mrz-val").textContent  = mrzLabels(mrz);
    $("st-swap-val").textContent = swapLabels(swap);
    $("st-corr-val").textContent = corrLabels(corr);

    // Predicted outcome (rough heuristic)
    const acc = Math.max(0.5, 1 - 0.09 * cer - 0.05 * corr);
    let prediction;
    if (swap >= 0.15) prediction = `Active date-swap attack. MRZ must catch every forgery — if MRZ readable rate is low, expect escapes.`;
    else if (mrz <= 0.5) prediction = `Blown MRZ. Most docs will route to <em>recapture</em> — cost lands on the customer's app, not the branch.`;
    else if (cer <= 0.15) prediction = `Bank-tuned OCR. Expect ~${((1-acc)*100).toFixed(0)}% docs to review, <em>0% escape rate</em>.`;
    else prediction = `Baseline. Expect ~${((1-acc)*100).toFixed(0)}% docs to review, <em>0% escape rate</em>.`;
    $("st-preview-txt").innerHTML = prediction;
  };
  ["st-cer","st-mrz","st-swap","st-corr"].forEach(id => $(id).addEventListener("input", update));
  update();

  const paceGraphAll = (activeStage) => {
    // Reuse the same graph shape as scenarios but into st-pipeline-svg
    const svg = $("st-pipeline-svg");
    if (!svg) return;
    // Manually clone-render by temporarily swapping IDs
    const original = document.getElementById("pipeline-svg");
    const originalId = original ? original.id : null;
    svg.id = "pipeline-svg";
    if (original) original.id = "pipeline-svg-orig";
    drawPipelineGraph(activeStage);
    svg.id = "st-pipeline-svg";
    if (original) original.id = originalId;
  };
  paceGraphAll(null);

  let stStream = null;
  $("st-run").addEventListener("click", async () => {
    const n = +$("st-n").value || 20;
    const params = new URLSearchParams({
      n, anchor: true, scenario: "baseline",
      cer_multiplier: $("st-cer").value,
      mrz_readable_rate: $("st-mrz").value,
      date_swap_rate: $("st-swap").value,
      engine_b_correlation: $("st-corr").value,
      pace_ms: 100,
    });

    $("st-progress-card").hidden = false;
    $("st-anchor-card").hidden = true;
    $("st-cot").innerHTML = "";
    $("st-inspect").innerHTML = '<em class="muted">Waiting for first customer…</em>';
    ["st-accept","st-review","st-recap","st-esc"].forEach(id =>
      $(id).textContent = id === "st-esc" ? "0.0000%" : "0");
    $("st-bar").style.width = "0%";

    const es = new EventSource(`/api/issue/stream?${params}`);
    stStream = es;
    $("st-run").disabled = true;

    const cotEl = $("st-cot");
    const cotLineSt = (cls, html) => {
      const line = el("div", "cot-line " + cls);
      line.innerHTML = html;
      cotEl.appendChild(line);
      cotEl.scrollTop = cotEl.scrollHeight;
      while (cotEl.children.length > 200) cotEl.removeChild(cotEl.firstChild);
    };
    cotLineSt("doc-head", `▶ Running <b>${escapeHtml($("st-name").value)}</b> · n=${n}`);

    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.type === "stage") {
        paceGraphAll(e.stage);
        $("st-progress-txt").textContent = `${e.index + 1}/${e.total} · ${e.stage}`;
        cotLineSt("stage", `<span class="stage-name">${e.stage.toUpperCase()}</span> ${escapeHtml(e.detail)}`);
      } else if (e.type === "doc") {
        $("st-bar").style.width = ((e.index + 1) / e.total * 100) + "%";
        const rs = e.running_stats;
        $("st-accept").textContent = rs.auto_accepted;
        $("st-review").textContent = rs.reviewed;
        $("st-recap").textContent = rs.recapture;
        $("st-esc").textContent = rs.field_accepted
          ? fmtPct4(rs.escapes / (rs.field_accepted + rs.field_reviewed)) : "0.0000%";
        // Render current doc into st-inspect using existing renderer
        const tmp = $("doc-inspect");
        // temporarily swap ids so renderDocInspect targets our div
        if (tmp) tmp.id = "doc-inspect-orig";
        $("st-inspect").id = "doc-inspect";
        renderDocInspect(e);
        $("doc-inspect").id = "st-inspect";
        if (tmp) tmp.id = "doc-inspect";
        const custShort = escapeHtml(((e.truth || {}).name || "").slice(0, 24));
        const cls = e.action === "issue" ? "decision-ok" : e.action === "recapture" ? "decision-bad" : "";
        cotLineSt(cls, `${e.action === "issue" ? "✓ AUTO-ISSUED" : e.action === "recapture" ? "✗ RECAPTURE" : "⚠ TO REVIEWER"} · ${custShort}`);
      } else if (e.type === "anchor") {
        const r = e.receipt;
        cotLineSt("stage", `<span class="stage-name">ANCHOR</span> epoch ${e.epoch_id} · ${r.credential_count} customers in one Base tx`);
        $("st-anchor-card").hidden = false;
        $("st-anchor-receipt").innerHTML = `
          <div class="k">Epoch ID</div><div class="v mono">${e.epoch_id}</div>
          <div class="k">Epoch root</div><div class="v mono">${r.epoch_root_hex}</div>
          <div class="k">Customers</div><div class="v">${r.credential_count} in one tx</div>
          <div class="k">Gas used</div><div class="v mono">${r.gas_used.toLocaleString()}</div>
          <div class="k">Cost</div><div class="v">$${(r.cost_usd || 0).toFixed(6)}</div>
          <div class="k">Basescan</div><div class="v"><a href="${r.basescan_url}" target="_blank" class="mono">${shortHex(r.tx_hash)}</a></div>`;
      } else if (e.type === "done") {
        $("st-progress-txt").textContent = `done · ${e.stats.documents} docs · ${e.stats.escapes} escapes`;
        cotLineSt("doc-head", `■ Run complete`);
        paceGraphAll(null);
        es.close(); stStream = null;
        $("st-run").disabled = false;
      }
    };
    es.onerror = () => { es.close(); stStream = null; $("st-run").disabled = false; };
  });

  $("st-save").addEventListener("click", () => {
    const name = $("st-name").value || "My scenario";
    const preset = {
      cer_multiplier: +$("st-cer").value,
      mrz_readable_rate: +$("st-mrz").value,
      date_swap_rate: +$("st-swap").value,
      engine_b_correlation: +$("st-corr").value,
    };
    const list = JSON.parse(localStorage.getItem("finfabric.presets") || "[]");
    list.push({ name, ...preset, savedAt: Date.now() });
    localStorage.setItem("finfabric.presets", JSON.stringify(list));
    $("st-saved-hint").textContent = `Saved "${name}" locally.`;
  });
};

// ============================================================
// CREDENTIALS
// ============================================================
controllers.credentials = async () => {
  const epochs = await api("/api/epochs");
  state.epochs = epochs;
  const sel = $("cred-epoch");
  sel.innerHTML = "";
  for (const e of epochs) {
    const opt = el("option");
    opt.value = e.epoch_id;
    opt.textContent = `Epoch ${e.epoch_id} — ${e.credential_count} creds — ${fmtDate(e.anchored_at)}`;
    sel.appendChild(opt);
  }
  const query = location.hash.split("?")[1] || "";
  const params = new URLSearchParams(query);
  if (params.get("epoch")) sel.value = params.get("epoch");

  const s = { offset: 0, limit: 25 };
  async function load() {
    const q = $("cred-q").value;
    const status = $("cred-status").value;
    const r = await api(`/api/epochs/${sel.value}/credentials?offset=${s.offset}&limit=${s.limit}&status=${status}&q=${encodeURIComponent(q)}`);
    const tbody = $("cred-table").querySelector("tbody");
    tbody.innerHTML = "";
    for (const c of r.items) {
      const tr = el("tr");
      tr.innerHTML = `
        <td class="mono">${c.index}</td>
        <td class="mono">${c.doc_id}</td>
        <td>${escapeHtml(c.values.name)}</td>
        <td class="mono">${c.values.date_of_birth}</td>
        <td class="mono">${c.values.nationality}</td>
        <td>${c.revoked ? '<span class="tag bad">revoked</span>' : '<span class="tag good">active</span>'}</td>
        <td class="mono">${shortHex(c.root_hex)}</td>
        <td><span class="muted">open ›</span></td>`;
      tr.addEventListener("click", () => openCredentialDrawer(sel.value, c.index));
      tbody.appendChild(tr);
    }
    $("cred-pager").innerHTML = `${s.offset + 1}–${Math.min(s.offset + s.limit, r.total)} of ${r.total}
      <button class="btn ghost" ${s.offset === 0 ? "disabled" : ""} id="pg-prev">←</button>
      <button class="btn ghost" ${s.offset + s.limit >= r.total ? "disabled" : ""} id="pg-next">→</button>`;
    $("pg-prev")?.addEventListener("click", () => { s.offset = Math.max(0, s.offset - s.limit); load(); });
    $("pg-next")?.addEventListener("click", () => { s.offset += s.limit; load(); });
  }
  sel.addEventListener("change", () => { s.offset = 0; load(); });
  $("cred-status").addEventListener("change", () => { s.offset = 0; load(); });
  $("cred-q").addEventListener("input", debounce(() => { s.offset = 0; load(); }, 200));
  await load();
};

async function openCredentialDrawer(epochId, index) {
  const d = await api(`/api/credentials/${epochId}/${index}`);
  openDrawer(`Credential #${index}  ·  epoch ${epochId}`);
  const tpl = document.getElementById("tpl-drawer-credential").content.cloneNode(true);
  $("drawer-body").innerHTML = "";
  $("drawer-body").appendChild(tpl);

  const kvs = $("cred-kvs");
  const c = d.credential, doc = d.doc, ep = d.epoch;
  const rows = [
    ["Doc ID", c.doc_id],
    ["Subject DID", `<span class="mono">${c.subject_did}</span>`],
    ["Credential root", `<span class="mono">${c.root_hex}</span>`],
    ["Epoch root", `<span class="mono">${ep.root_hex}</span>`],
    ["Epoch", `${ep.epoch_id}`],
    ["Status", c.revoked ? '<span class="tag bad">revoked</span>' : '<span class="tag good">active</span>'],
    ["Anchored tx", `<a class="mono" target="_blank" href="${ep.basescan_url}">${shortHex(ep.tx_hash)}</a>`],
    ["Latency @ issue", `${doc.latency_ms}ms`],
  ];
  kvs.innerHTML = rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join("");

  const fb = $("drawer-fields-body");
  fb.innerHTML = "";
  const proofFieldSel = $("drawer-proof-field");
  proofFieldSel.innerHTML = "";
  for (const dec of doc.decisions) {
    const tr = el("tr");
    tr.innerHTML = `
      <td><b>${dec.label}</b><div class="muted">${dec.field}</div></td>
      <td class="mono">${escapeHtml(dec.value ?? "—")}</td>
      <td class="mono">${dec.correct ? '' : '<span class="tag bad">≠</span> '}${escapeHtml(dec.truth)}</td>
      <td class="signals-cell">${signalsHtml(dec)}</td>
      <td><button class="btn ghost">why</button></td>`;
    tr.querySelector("button").addEventListener("click", async () => {
      const r = await api("/api/explain", { field: dec.field, cand_a: dec.ocr_a, cand_b: dec.ocr_b,
        conf: dec.confidence, signals: dec.signals, reason: dec.reason });
      alert(r.explanation);
    });
    fb.appendChild(tr);

    if (dec.accepted) {
      const opt = el("option"); opt.value = dec.field; opt.textContent = dec.label;
      proofFieldSel.appendChild(opt);
    }
  }

  const rg = $("drawer-reveal");
  rg.innerHTML = "";
  const revealSet = new Set(["date_of_birth", "nationality"]);
  for (const dec of doc.decisions.filter(d => d.accepted)) {
    const cell = el("label", "reveal-cell" + (revealSet.has(dec.field) ? " on" : ""));
    const cb = el("input"); cb.type = "checkbox"; cb.checked = revealSet.has(dec.field); cb.dataset.name = dec.field;
    cb.addEventListener("change", () => cell.classList.toggle("on", cb.checked));
    const span = el("span", "", dec.label);
    cell.appendChild(cb); cell.appendChild(span);
    rg.appendChild(cell);
  }

  $("drawer-verify").addEventListener("click", () => runVerify(epochId, index, false));
  $("drawer-tamper").addEventListener("click", () => runVerify(epochId, index, true));
  $("drawer-revoke").addEventListener("click", async () => {
    if (!confirm("Revoke this credential? A new status list will be published.")) return;
    const r = await api("/api/revoke", { epoch_id: +epochId, credential_index: +index });
    openCredentialDrawer(epochId, index);
    alert(`Revoked. Tx ${r.receipt.tx_hash.slice(0, 20)}…, v${r.receipt.version}. One tx covers ${r.total_credentials.toLocaleString()} creds.`);
  });
  $("drawer-copy-did").addEventListener("click", () => navigator.clipboard.writeText(c.subject_did));
  $("drawer-proof-load").addEventListener("click", () => loadProof(epochId, index, proofFieldSel.value));
}

function signalsHtml(dec) {
  return ["schema","agreement","confidence","mrz","adjudicator"].map(k => {
    const v = dec.signals[k];
    const cls = v === true ? "ok" : v === false ? "no" : "na";
    return `<span class="signal ${cls}" title="${k}: ${v === true ? "passed" : v === false ? "failed" : "n/a"}">${k[0]}</span>`;
  }).join("");
}

async function runVerify(epochId, index, tamper) {
  const reveal = [...$$("#drawer-reveal input:checked")].map(x => x.dataset.name);
  if (!reveal.length) { alert("Select at least one field to reveal."); return; }
  const body = { epoch_id: +epochId, credential_index: +index, reveal };
  if (tamper) { body.tamper_field = "date_of_birth"; body.tamper_value = "1990-01-01"; }
  const r = await api("/api/disclose", body);
  const out = $("drawer-verify-out");
  out.classList.remove("ok", "bad");
  out.classList.add(r.verified ? "ok" : "bad");
  const revealedTxt = Object.entries(r.revealed).map(([k, v]) => `${k}=${v}`).join("; ");
  out.innerHTML = r.verified
    ? `<b>✓ Verified against on-chain root</b> ${shortHex(r.on_chain_root)}<br/>
       Revealed: ${escapeHtml(revealedTxt)}<br/>
       Withheld: ${r.withheld_field_count} fields, <b>${r.presentation_bytes}</b> bytes on the wire.`
    : `<b>✗ Rejected:</b> ${escapeHtml(r.reason)}<br/>
       The Merkle path from the leaf does not resolve to the anchored epoch root.`;
}

async function loadProof(epochId, index, field) {
  if (!field) { alert("Pick an accepted field."); return; }
  const p = await api(`/api/credentials/${epochId}/${index}/proof/${field}`);
  const box = $("drawer-proof-view");
  const rows = [];
  rows.push(`<div class="proof-step leaf">
    <div class="k">leaf preimage</div>
    <div class="h">field=<b>${p.leaf_preimage.field_name}</b> · value=<b>${escapeHtml(p.leaf_preimage.value)}</b> · salt=<b>${p.leaf_preimage.salt_hex.slice(0,16)}…</b></div>
  </div>`);
  for (let i = 0; i < p.credential_walk.length; i++) {
    const step = p.credential_walk[i];
    if (i === 0) {
      rows.push(`<div class="proof-step">
        <div class="k">H⁰ · sha256(preimage) = leaf hash</div>
        <div class="h">${step.hash}</div>
      </div>`);
    } else {
      rows.push(`<div class="arrow">↓ combined with sibling ${step.sibling_is_left ? "on the left" : "on the right"}</div>`);
      rows.push(`<div class="proof-step">
        <div class="k">H<sup>${step.level}</sup> · sibling ${step.sibling.slice(0, 24)}…</div>
        <div class="h">= ${step.hash}</div>
      </div>`);
    }
  }
  rows.push(`<div class="arrow">↓ credential root</div>`);
  rows.push(`<div class="proof-step"><div class="k">credential root</div><div class="h">${p.credential_root}</div></div>`);
  if (p.epoch_walk?.length) rows.push(`<div class="arrow">↓ folded into epoch tree (${p.epoch_walk.length} sibling${p.epoch_walk.length > 1 ? "s" : ""})</div>`);
  rows.push(`<div class="proof-anchor">on-chain epoch root · ${p.epoch_root}</div>`);
  box.innerHTML = rows.join("");
}

// ============================================================
// REVIEWS
// ============================================================
controllers.reviews = async () => {
  const epochs = await api("/api/epochs");
  const sel = $("rev-epoch");
  sel.innerHTML = "";
  for (const e of epochs) {
    const opt = el("option");
    opt.value = e.epoch_id;
    opt.textContent = `Epoch ${e.epoch_id} — ${e.reviewed_count} in review`;
    sel.appendChild(opt);
  }
  async function load() {
    const r = await api(`/api/epochs/${sel.value}/reviews?limit=200`);
    const tbody = $("rev-table").querySelector("tbody");
    tbody.innerHTML = "";
    for (const it of r.items) {
      const reasonsTxt = Object.entries(it.reasons).map(([k, v]) => `${k}: ${v}`).join("  •  ");
      const actionTag = it.action === "recapture" ? '<span class="tag bad">recapture</span>' : '<span class="tag warn">review</span>';
      const tr = el("tr");
      tr.innerHTML = `
        <td class="mono">${it.doc_id}</td>
        <td>${actionTag}</td>
        <td>${it.mrz_ok ? '<span class="tag good">ok</span>' : '<span class="tag bad">bad</span>'}</td>
        <td class="mono">${it.reviewed_fields.join(", ")}</td>
        <td class="muted">${escapeHtml(reasonsTxt)}</td>`;
      tbody.appendChild(tr);
    }
  }
  sel.addEventListener("change", load);
  await load();
};

// ============================================================
// ADJUDICATOR
// ============================================================
controllers.adjudicator = async () => {
  $("run-adj").addEventListener("click", async () => {
    const btn = $("run-adj");
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Thinking';
    try {
      const r = await api("/api/adjudicate", {
        field: $("adj-field").value,
        cand_a: $("adj-a").value,
        cand_b: $("adj-b").value,
        mrz_hint: $("adj-mrz").value,
        conf_a: parseFloat($("adj-conf").value),
        validator: $("adj-validator").value,
      });
      const box = $("adj-result");
      box.classList.remove("err", "ok");
      if (r.abstained) {
        box.classList.add("err");
        box.innerHTML = `Adjudicator abstained. This field would escalate to a human. <span class="muted">Model: ${r.model}</span>`;
      } else {
        box.classList.add("ok");
        box.innerHTML = `Chose <strong>${escapeHtml(r.chosen)}</strong> — third opinion accepted, field would be issued. <span class="muted">Model: ${r.model}</span>`;
      }
    } catch (e) {
      $("adj-result").classList.add("err");
      $("adj-result").textContent = "Error: " + e.message;
    } finally {
      btn.disabled = false; btn.textContent = "Ask the VLM";
    }
  });

  $("load-real-adj").addEventListener("click", async () => {
    const epochs = await api("/api/epochs");
    if (!epochs.length) return alert("No epochs yet.");
    for (const ep of epochs) {
      const r = await api(`/api/epochs/${ep.epoch_id}/reviews?limit=50`);
      if (!r.items.length) continue;
      // pick the first review with a disagreement OCR pair
      const it = r.items[0];
      const detail = await api(`/api/credentials/${ep.epoch_id}/0`).catch(() => null);
      if (!detail) continue;
      const bad = detail.doc.decisions.find(d => !d.accepted && d.ocr_a && d.ocr_b && d.ocr_a !== d.ocr_b);
      if (!bad) continue;
      $("adj-field").value = bad.field;
      $("adj-a").value = bad.ocr_a;
      $("adj-b").value = bad.ocr_b;
      $("adj-conf").value = bad.confidence.toFixed(2);
      $("adj-mrz").value = bad.mrz_value || "";
      $("adj-validator").value = bad.validator;
      return;
    }
    alert("No disagreements found in current epochs — run a scenario first.");
  });
};

// ============================================================
// AUDIT
// ============================================================
controllers.audit = async () => {
  $("run-report").addEventListener("click", async () => {
    const btn = $("run-report");
    btn.disabled = true; btn.innerHTML = '<span class="spin dark"></span> Writing';
    try {
      const r = await api("/api/report", {});
      const box = $("audit-report");
      box.innerHTML = renderMarkdown(r.report_markdown);
      if (r.is_fallback) box.insertAdjacentHTML("beforeend", `<div class="muted">(deterministic template — ${r.model})</div>`);
    } catch (e) {
      $("audit-report").innerHTML = `<em class="muted">${escapeHtml(e.message)}</em>`;
    } finally {
      btn.disabled = false; btn.textContent = "Regenerate";
    }
  });

  const d = await api("/api/dashboard");
  const rt = $("rev-timeline");
  rt.innerHTML = "";
  if (!d.revocations.length) rt.innerHTML = "<li class=muted>No revocations yet.</li>";
  for (const r of d.revocations) {
    const li = el("li", "status");
    li.innerHTML = `
      <div class="tl-time">${fmtTime(r.receipt.anchored_at)}</div>
      <div class="tl-body">
        <b>Epoch ${r.epoch_id}</b> · v${r.version} · ${r.cred_indices.length} revoked<br/>
        list hash <span class="mono">${shortHex(r.list_hash_hex)}</span> · <a target="_blank" href="${r.receipt.basescan_url}" class="mono">${shortHex(r.receipt.tx_hash)}</a>
      </div>`;
    rt.appendChild(li);
  }
  const tt = $("tx-table");
  tt.innerHTML = "";
  for (const tx of d.chain_txs) {
    const isAnchor = tx.kind === "anchor";
    const tr = el("tr");
    tr.innerHTML = `
      <td><span class="tag ${isAnchor ? "info" : "warn"}">${tx.kind}</span></td>
      <td class="mono">${tx.epoch_id || "—"}</td>
      <td class="mono">${tx.version || "—"}</td>
      <td class="mono">${shortHex(tx.epoch_root_hex || tx.list_hash_hex || "")}</td>
      <td class="mono">${(tx.gas_used || 0).toLocaleString()}</td>
      <td>${fmtTime(tx.anchored_at)}</td>
      <td><a href="${tx.basescan_url}" target="_blank" class="mono">${shortHex(tx.tx_hash)}</a></td>`;
    tt.appendChild(tr);
  }
};

// ============================================================
// GLOBAL ASSISTANT (present on every view)
// ============================================================
const suggestions = {
  overview: [
    "How does this reduce IDBI's KYC cost per customer?",
    "Why is the escape rate the number that matters for a bank?",
    "How does this align with the DPDP Act?",
  ],
  scenarios: [
    "Which scenario best models a fintech partner batch?",
    "What happens if the customer's phone photo has glare?",
    "How does the gate defend against expiry-date forgery?",
  ],
  studio: [
    "What CER should I set for a realistic Indian bank OCR?",
    "How do I model an active fraud attack?",
    "How much on-chain cost per 100 customers?",
  ],
  credentials: [
    "What's inside a customer record besides the field values?",
    "Why do salts stay in the customer's wallet, not on-chain?",
    "How does the Merkle path prove one field without the others?",
  ],
  reviews: [
    "Why does the address field cause most reviewer load?",
    "What's the difference between review and recapture for a customer?",
    "How would fine-tuned OCR change this queue?",
  ],
  adjudicator: [
    "When does the gate call the VLM instead of a human?",
    "Why is a VLM's error uncorrelated with OCR errors?",
    "What happens when the adjudicator abstains?",
  ],
  audit: [
    "Summarise the last epoch for an RBI reviewer.",
    "What would a compromised issuer key affect?",
    "How does the revocation timeline satisfy PMLA requirements?",
  ],
};

function wireAssistant() {
  $("asst-fab").addEventListener("click", toggleAssistant);
  $("asst-close").addEventListener("click", closeAssistant);
  $("chat-form").addEventListener("submit", onChatSubmit);
  updateAssistantContext();
}
function openAssistant() {
  $("asst-panel").classList.add("open");
  $("asst-panel").setAttribute("aria-hidden", "false");
  $("asst-fab").classList.add("open");
  setTimeout(() => $("chat-input").focus(), 200);
}
function closeAssistant() {
  $("asst-panel").classList.remove("open");
  $("asst-panel").setAttribute("aria-hidden", "true");
  $("asst-fab").classList.remove("open");
}
function toggleAssistant() {
  if ($("asst-panel").classList.contains("open")) closeAssistant();
  else openAssistant();
}
function updateAssistantContext() {
  $("asst-context") && ($("asst-context").textContent = `Context: ${state.currentView}`);
  const wrap = $("asst-suggest");
  if (!wrap) return;
  wrap.innerHTML = "";
  for (const s of (suggestions[state.currentView] || [])) {
    const b = el("button", "chip", s);
    b.type = "button";
    b.addEventListener("click", () => {
      $("chat-input").value = s;
      $("chat-form").dispatchEvent(new Event("submit"));
    });
    wrap.appendChild(b);
  }
}
async function onChatSubmit(e) {
  e.preventDefault();
  const input = $("chat-input");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  const log = $("chat-log");
  log.appendChild(el("div", "chat-msg user", msg));
  log.scrollTop = log.scrollHeight;
  const thinking = el("div", "chat-msg bot", "…");
  log.appendChild(thinking);
  log.scrollTop = log.scrollHeight;
  try {
    const r = await api("/api/chat", {
      message: `${msg}\n\n(User is currently viewing: ${state.currentView})`,
      history: state.chatHistory,
    });
    thinking.textContent = r.answer;
    if (r.is_fallback) thinking.classList.add("err");
    state.chatHistory.push({role: "user", content: msg}, {role: "assistant", content: r.answer});
  } catch (err) {
    thinking.textContent = "Error: " + err.message;
    thinking.classList.add("err");
  }
  log.scrollTop = log.scrollHeight;
}

// ============================================================
// ONBOARDING
// ============================================================
const introSlides = [
  {
    title: "FinFabric — decentralized KYC for Indian banks",
    body: `
      <p>Every bank re-does KYC from scratch, pays humans to re-verify the same documents, stores PII in databases that eventually leak, and pays per-transaction fees to revoke lapsed customers. FinFabric replaces all four of those with one design.</p>
      <div class="slide-vis">
        <strong>What this demo shows:</strong> onboard a customer once, share the KYC with any relying party (partner bank / fintech / merchant) without re-KYC, prove selected fields on demand ("customer is over 18", "PAN is valid") without leaking the rest, and revoke instantly on a sanctions-list update — with <strong>zero PII on-chain</strong>, aligned to RBI Master Direction on KYC and India's DPDP Act.
      </div>
      <p>Structurally we follow BDIMS (Le et al., <em>Computers</em> 2025) but replace the three choices that make BDIMS unfit for Indian banking: paid human verifiers, image hashes, and per-user gas.</p>`,
  },
  {
    title: "The five-signal gate",
    body: `
      <p>Instead of routing every document to a KYC officer, the gate stacks five independent signals. A field auto-issues only when they all agree — and every auto-issued field ends up on-chain, so the standard of proof has to be higher than "one recognizer said so."</p>
      <ul>
        <li><strong>Schema</strong> — value passes a deterministic validator (PAN pattern, PIN check, date parse)</li>
        <li><strong>Agreement</strong> — two OCR engines produce the same canonical value</li>
        <li><strong>Confidence</strong> — recognizer's own score clears threshold</li>
        <li><strong>MRZ</strong> — machine-readable-zone check digits agree (catches forged dates)</li>
        <li><strong>Consistency</strong> — cross-field ordering (KYC issued ≤ valid till, DOB before issue)</li>
      </ul>
      <p>What we optimize is the <em>escape rate</em> — wrong fields that got auto-accepted. A wrong field caught by review costs a KYC officer ten seconds. A wrong field <em>accepted</em> is a compliance exposure the bank owns forever.</p>`,
  },
  {
    title: "How to run this demo",
    body: `
      <ul>
        <li><strong>Scenarios</strong> — six banking-native cases: new account opening, fine-tuned in-branch OCR, adversarial expiry-date forgery, poor phone photo, correlated OCR failures, high-volume batch. Tweak every knob and run.</li>
        <li><strong>Credentials</strong> — every anchored customer record. Click any row to see per-field gate decisions, verify a selective disclosure ("prove KYC valid till 2030 without leaking DOB"), revoke, or step through the Merkle proof from leaf to on-chain root.</li>
        <li><strong>KYC officer queue</strong> — everything the gate could not auto-accept, with per-field reasons.</li>
        <li><strong>Adjudicator</strong> — live third-opinion VLM (Gemini) that sees only two disagreeing OCR reads — never the customer's file.</li>
        <li><strong>Audit</strong> — a compliance report grounded in current state, plus the revocation and anchor timeline for an RBI reviewer.</li>
      </ul>
      <div class="slide-vis">
        <strong>The assistant</strong> is on every screen — bottom-right button, or press <code>⌘/</code>. Ask "why did this record go to review?" or "explain the DPDP posture" from anywhere.
      </div>`,
  },
];
let introIndex = 0;

function wireIntro() {
  $("intro-next").addEventListener("click", () => showIntro(introIndex + 1));
  $("intro-prev").addEventListener("click", () => showIntro(introIndex - 1));
  $("intro-skip").addEventListener("click", closeIntro);
  $("show-intro").addEventListener("click", openIntro);
  $("intro-modal").addEventListener("click", (e) => { if (e.target === $("intro-modal")) closeIntro(); });
}
function openIntro() { introIndex = 0; showIntro(0); $("intro-modal").classList.add("open"); }
function closeIntro() {
  $("intro-modal").classList.remove("open");
  localStorage.setItem("finfabric.intro.seen", "1");
}
function showIntro(idx) {
  if (idx < 0) idx = 0;
  if (idx >= introSlides.length) { closeIntro(); return; }
  introIndex = idx;
  const s = introSlides[idx];
  $("intro-body").innerHTML = `<div class="slide"><h3>${s.title}</h3>${s.body}</div>`;
  $("intro-dots").innerHTML = introSlides.map((_, i) =>
    `<span class="dot ${i === idx ? "on" : ""}"></span>`).join("");
  $("intro-prev").disabled = idx === 0;
  $("intro-next").textContent = idx === introSlides.length - 1 ? "Start exploring" : "Next";
}

// ============================================================
// misc
// ============================================================
function renderMarkdown(md) {
  const esc = escapeHtml(md);
  return esc
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/^\- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.+<\/li>\n?)+/g, m => "<ul>" + m + "</ul>")
    .replace(/\n\n+/g, "</p><p>")
    .replace(/^([^<].+)$/gm, "<p>$1</p>")
    .replace(/<p><\/p>/g, "");
}

boot();
