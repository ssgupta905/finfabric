// Comprehensive autoplay tour (~2:45). Covers every FinFabric feature:
// Overview → Extraction (VLM/OCR overlays) → Scenarios → Studio copilot
// generates a workflow → CSV upload + PDF → Credentials drill-down with
// Merkle proof + tamper → Adjudicator (Gemini) → Audit report → close.
// Activated by ?autoplay=1.

(function () {
  const params = new URLSearchParams(location.search);
  if (params.get("autoplay") !== "1") return;

  // ---------- overlay chrome ----------
  const overlay = document.createElement("div");
  overlay.className = "autoplay-overlay";
  overlay.innerHTML = `
    <div class="autoplay-badge">
      <span class="rec-dot"></span>
      <span>Autoplay demo</span>
      <span class="autoplay-time" id="ap-time">0:00</span>
      <button class="autoplay-skip" id="ap-skip">Skip</button>
    </div>
    <div class="autoplay-caption" id="ap-caption">
      <div class="ap-title" id="ap-title"></div>
      <div class="ap-body" id="ap-body"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  const style = document.createElement("style");
  style.textContent = `
    .autoplay-overlay { position: fixed; inset: 0; pointer-events: none; z-index: 400; }
    .autoplay-badge {
      position: absolute; top: 14px; right: 20px;
      display: flex; align-items: center; gap: 10px;
      background: rgba(29,29,31,0.9); color: white;
      padding: 6px 14px; border-radius: 999px;
      font: 500 12px -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
      backdrop-filter: blur(10px);
      pointer-events: auto;
    }
    .rec-dot { width: 8px; height: 8px; border-radius: 50%; background: #ff3b30;
               animation: rec-pulse 1.4s ease-in-out infinite; }
    @keyframes rec-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
    .autoplay-time { font-family: ui-monospace, "SF Mono", Menlo, monospace; opacity: 0.7; }
    .autoplay-skip {
      appearance: none; border: 1px solid rgba(255,255,255,0.3);
      background: transparent; color: white;
      padding: 3px 10px; border-radius: 999px;
      font-size: 11px; cursor: pointer;
    }
    .autoplay-skip:hover { background: rgba(255,255,255,0.15); }
    .autoplay-caption {
      position: absolute; left: 50%; bottom: 40px; transform: translate(-50%, 20px);
      max-width: 780px; width: calc(100% - 80px);
      background: linear-gradient(135deg, rgba(0,113,227,0.98) 0%, rgba(120,86,255,0.98) 100%);
      color: white; padding: 18px 24px; border-radius: 16px;
      box-shadow: 0 12px 40px rgba(0,0,0,0.25), 0 0 0 1px rgba(255,255,255,0.15) inset;
      backdrop-filter: blur(8px);
      opacity: 0; transition: opacity 0.28s, transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
    }
    .autoplay-caption.show { opacity: 1; transform: translate(-50%, 0); }
    .ap-title { font: 600 16px -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif; margin-bottom: 4px; }
    .ap-body { font: 400 13px -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif; line-height: 1.5; opacity: 0.95; }
    .ap-body em { font-style: normal; font-weight: 600; text-decoration: underline; text-underline-offset: 3px; }
    .ap-highlight-ring {
      position: absolute; border: 3px solid #ff9500; border-radius: 12px;
      pointer-events: none; z-index: 399;
      box-shadow: 0 0 0 4px rgba(255,149,0,0.25), 0 0 30px rgba(255,149,0,0.5);
      transition: all 0.28s cubic-bezier(0.22, 1, 0.36, 1);
      opacity: 0;
    }
    .ap-highlight-ring.show { opacity: 1; }
  `;
  document.head.appendChild(style);

  const highlight = document.createElement("div");
  highlight.className = "ap-highlight-ring";
  document.body.appendChild(highlight);

  const timeEl = document.getElementById("ap-time");
  const captionEl = document.getElementById("ap-caption");
  const titleEl = document.getElementById("ap-title");
  const bodyEl = document.getElementById("ap-body");
  const startedAt = Date.now();
  setInterval(() => {
    const s = Math.floor((Date.now() - startedAt) / 1000);
    timeEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 250);

  // ---------- primitives ----------
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  async function caption(title, body, holdMs = 3000) {
    captionEl.classList.remove("show");
    await sleep(120);
    titleEl.textContent = title;
    bodyEl.innerHTML = body;
    captionEl.classList.add("show");
    await sleep(holdMs);
  }
  function hideCaption() { captionEl.classList.remove("show"); highlight.classList.remove("show"); }
  function nav(hash) { location.hash = hash; return sleep(500); }
  function ring(selector, padding = 8) {
    const el = document.querySelector(selector);
    if (!el) { highlight.classList.remove("show"); return; }
    const r = el.getBoundingClientRect();
    highlight.style.top    = (r.top - padding) + "px";
    highlight.style.left   = (r.left - padding) + "px";
    highlight.style.width  = (r.width + padding * 2) + "px";
    highlight.style.height = (r.height + padding * 2) + "px";
    highlight.classList.add("show");
  }
  function scrollTo(selector, offset = 60) {
    const el = document.querySelector(selector);
    if (!el) return;
    const wrap = document.querySelector(".view-root");
    if (!wrap) return;
    const y = el.getBoundingClientRect().top + wrap.scrollTop - offset;
    wrap.scrollTo({ top: y, behavior: "smooth" });
  }
  function click(selector) { document.querySelector(selector)?.click(); }
  async function waitFor(selector, timeoutMs = 8000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      const el = document.querySelector(selector);
      if (el) return el;
      await sleep(120);
    }
    return null;
  }
  async function waitUntil(pred, timeoutMs = 30000, interval = 250) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      if (pred()) return true;
      await sleep(interval);
    }
    return false;
  }

  document.getElementById("ap-skip").addEventListener("click", () => location.href = location.pathname);

  // ---------- storyboard ----------
  async function run() {
    await waitFor(".side-link");
    await sleep(400);
    const modal = document.getElementById("intro-modal");
    if (modal && modal.classList.contains("open")) {
      document.getElementById("intro-skip")?.click();
      await sleep(300);
    }

    // ============================================================
    // 1. HERO (0:00 – 0:10)
    // ============================================================
    await nav("#/overview");
    await waitFor(".banner");
    ring(".banner");
    await caption(
      "FinFabric — decentralized KYC for Indian banks",
      "Onboard once, share across banks, prove one field, revoke a whole book. <em>Zero PII on-chain</em>. Built for the IDBI hackathon.",
      4000
    );
    scrollTo(".kpi-grid");
    ring(".kpi-grid");
    await caption("What we optimise", "<em>Escape rate 0.00%</em> — the fraud vector that matters — and cents of on-chain cost across the whole demo.", 3500);
    hideCaption();

    // ============================================================
    // 2. EXTRACTION — the "wow" visual (0:10 – 0:35)
    // ============================================================
    await nav("#/extraction");
    await waitFor("#ex-img");
    await sleep(500);
    ring(".ex-viewer");
    await caption(
      "Extraction studio · VLM + dual-OCR + MRZ",
      "This is what a real deployment sees. A classifier identifies the doc, layout detection finds each field, two OCR engines read each box, MRZ check-digits verify. Watch it run.",
      4500
    );
    // Kick off the analysis (auto-runs on view mount, but re-run for the recording)
    click("#ex-run");
    await sleep(8000);   // let the staged animation play through
    ring(".ex-panel");
    await caption(
      "Ten fields · nine issued · one to review",
      "Every field colour-coded: green for auto-issued, blue for MRZ-repaired, orange for engine disagreement, red for reviewer. Hover any row to spotlight the box on the card.",
      4500
    );
    hideCaption();

    // ============================================================
    // 3. SCENARIOS — 6-node pipeline runs live (0:35 – 1:05)
    // ============================================================
    await nav("#/scenarios");
    await waitFor(".scen-grid");
    await sleep(300);
    ring(".scen-grid");
    await caption(
      "Six banking scenarios · pick and configure",
      "Baseline OCR · in-branch fine-tuned · adversarial expiry forgery · blown MRZ · correlated engines · high-volume batch.",
      3800
    );
    click('.scen-card[data-key="baseline"]');
    await sleep(300);
    const pace = document.getElementById("scen-pace"); if (pace) pace.value = 80;
    const scenN = document.getElementById("scen-n"); if (scenN) scenN.value = 10;
    click("#scen-run");
    await sleep(500);
    ring("#pipeline-svg");
    await caption(
      "Six-stage pipeline · live",
      "Capture → Extract → Gate → Adjudicate → Commit → Anchor. Chain-of-thought streams doc-by-doc.",
      3800
    );
    await waitUntil(() => document.getElementById("run-progress-txt")?.textContent.startsWith("done"), 25000);
    scrollTo("#run-anchor-card");
    ring("#run-anchor-card");
    await caption("One tx anchors the batch", "10 credentials in a single Base transaction — same 32 bytes on-chain whether the batch is 10 or 10,000 customers.", 3800);
    hideCaption();

    // ============================================================
    // 4. STUDIO — Copilot generates a fresh workflow (1:05 – 1:40)
    // ============================================================
    await nav("#/studio");
    await waitFor(".studio-cols");
    await sleep(400);
    ring(".studio-left");
    await caption(
      "Studio · agentic workflow builder",
      "Compose banking capabilities. Save presets. Ask the copilot to generate a workflow from a plain-English brief.",
      4000
    );

    // Type into the copilot textarea (visible feel) then generate
    const cpDesc = document.getElementById("copilot-desc");
    if (cpDesc) {
      const target = "Onboard a corporate customer with GSTIN validation, IFSC check, PEP screen, AML risk score, then commit and anchor to Base.";
      cpDesc.value = "";
      cpDesc.focus();
      for (let i = 0; i < target.length; i += 3) {
        cpDesc.value = target.slice(0, i);
        await sleep(20);
      }
      cpDesc.value = target;
    }
    ring("#copilot-desc");
    await caption("Copilot brief", "Ask the copilot to compose a corporate onboarding workflow with GSTIN, PEP, AML.", 3000);
    click("#copilot-run");
    ring(".copilot-out");
    await caption("Gemini composes the graph…", "The prompt is grounded — the copilot must use our capabilities library and validate the JSON before it lands.", 4000);
    // Wait for the generated workflow to appear in the diagram
    await waitUntil(() => {
      const name = document.getElementById("wf-name")?.textContent || "";
      return !name.includes("No workflow loaded");
    }, 30000);
    await sleep(600);
    scrollTo("#wf-svg", 40);
    ring("#wf-svg");
    await caption(
      "Fresh workflow, ready to run",
      "The copilot wrote a valid topological workflow using only the capabilities registered in the platform. Each node runnable, savable, editable.",
      4200
    );
    hideCaption();

    // ============================================================
    // 5. STUDIO — Upload data + PDF (1:40 – 2:05)
    // ============================================================
    scrollTo(".wf-upload", 40);
    ring(".wf-upload");
    await caption(
      "Upload customer data",
      "Drop a CSV to run the workflow against real records. We'll simulate the upload with a synthetic corporate + retail batch.",
      3800
    );

    // Simulate upload: fetch sample CSV then POST it directly.
    const wf = (window.state?.workflow) || (function(){
      // Studio state is scoped inside the module — reach via preset list order.
      // Fall back to preset id 1 (retail seed) if we can't resolve.
      return null;
    })();
    const wfId = (function(){
      // Read from wf-name badge -> presets list to find the id
      // Simpler: query /api/workflows and pick the most recent (our copilot one).
      return null; // will be filled by fetch below
    })();
    let uploadOk = false;
    try {
      const wfs = await fetch("/api/workflows").then(r => r.json());
      const chosen = wfs[0]; // newest first (server orders by id desc)
      if (chosen) {
        const csvText = await fetch("/api/workflow/sample_csv").then(r => r.text());
        const fd = new FormData();
        fd.append("file", new Blob([csvText], { type: "text/csv" }), "sample.csv");
        const res = await fetch(`/api/workflow/upload_run?workflow_id=${chosen.id}`, {
          method: "POST", body: fd,
        });
        if (res.ok) {
          const data = await res.json();
          uploadOk = true;
          // Manually render the summary card the same way the UI does
          const summary = document.getElementById("wf-upload-summary");
          if (summary) {
            summary.hidden = false;
            summary.innerHTML = `
              <b>Batch complete.</b> Workflow "${chosen.name}" ran on ${data.total_records} customer records.
              <div class="kpis">
                <div class="kpi-mini good"><div class="n">${data.passed}</div><div class="l">auto-passed</div></div>
                <div class="kpi-mini ${data.flagged ? 'bad' : ''}"><div class="n">${data.flagged}</div><div class="l">flagged</div></div>
                <div class="kpi-mini bad"><div class="n">${data.critical}</div><div class="l">critical</div></div>
                <div class="kpi-mini"><div class="n">${data.total_records}</div><div class="l">total</div></div>
              </div>
              <a class="btn primary" href="/api/report/pdf/${data.run_id}" download>⬇ Download compliance PDF</a>
              <div class="muted" style="margin-top:8px">Every flagged record's PDF entry cites the specific RBI / DPDP / PMLA guidance and the SOP-recommended next step.</div>`;
          }
        }
      }
    } catch (e) { /* ignore */ }
    await sleep(800);
    if (uploadOk) {
      scrollTo("#wf-upload-summary");
      ring("#wf-upload-summary");
      await caption(
        "Batch complete · PDF ready",
        "Every flagged record's PDF entry cites the specific RBI Master Direction paragraph, DPDP Act clause, or PMLA section — and the SOP-recommended next action.",
        4500
      );
    }
    hideCaption();

    // ============================================================
    // 6. CREDENTIALS drill-down (2:05 – 2:25)
    // ============================================================
    await nav("#/credentials");
    await waitFor("#cred-table tbody tr");
    await sleep(400);
    document.querySelector("#cred-table tbody tr")?.click();
    await waitFor("#drawer.open");
    await sleep(500);
    ring(".drawer.open .drawer-fields");
    await caption(
      "Per-field signals · five green each",
      "Every anchored customer, per-field. Each field carries five green signals: schema, agreement, confidence, MRZ, adjudicator.",
      3800
    );
    click("#drawer-verify"); await sleep(900);
    ring("#drawer-verify-out");
    await caption("Selective disclosure", "Reveal only two fields. Verified against the on-chain root. The other eight — not sent, not hashed.", 3500);
    click("#drawer-tamper"); await sleep(900);
    ring("#drawer-verify-out");
    await caption("Try to lie · rejected", "Tampered DOB → Merkle path breaks. Cryptographic integrity, not a policy check.", 3200);
    hideCaption();
    click("#drawer-close"); await sleep(300);

    // ============================================================
    // 7. AI features rapid (2:25 – 2:50)
    // ============================================================
    await nav("#/adjudicator");
    await waitFor(".adj-form");
    await sleep(300);
    ring(".adj-form");
    click("#run-adj");
    await caption("VLM adjudicator · live Gemini", "Third opinion for the ~5% the gate can't resolve. Uncorrelated with OCR errors by design.", 3500);
    await sleep(3000);
    ring("#adj-result");
    await caption("Chose the plausible value", "Real API call to Gemini 2.5 Flash. Human officer only if the model also abstains.", 3000);

    await nav("#/audit");
    await waitFor("#audit-report");
    await sleep(300);
    click("#run-report");
    ring(".card:has(#audit-report)");
    await caption("Compliance report · grounded", "Gemini reads live state and writes an RBI-ready audit note. Every number appears in the JSON we passed in.", 4200);
    await sleep(3500);

    click("#asst-fab"); await sleep(500);
    ring(".asst-panel.open");
    await caption("Assistant on every screen", "⌘/ shortcut. Grounded in the current state — never open-web knowledge.", 3200);
    click("#asst-close"); await sleep(200);

    // ============================================================
    // 8. CLOSE (2:50 – 3:00)
    // ============================================================
    await nav("#/overview");
    await waitFor(".banner");
    await sleep(200);
    ring(".banner");
    await caption(
      "FinFabric",
      "Onboard once. Prove one field. Revoke instantly. <em>No paid verifiers · no image hashes · no per-user gas.</em>",
      5000
    );
    hideCaption();
  }

  window.addEventListener("load", () => setTimeout(run, 800));
  if (document.readyState === "complete") setTimeout(run, 800);
})();
