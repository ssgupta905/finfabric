// Autoplay demo driver (short cut, target 1:50). Activated by ?autoplay=1.
// Compressed captions, rapid scenario montage, includes Studio + AI features,
// designed for a hackathon judge in a hurry.

(function () {
  const params = new URLSearchParams(location.search);
  if (params.get("autoplay") !== "1") return;

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
      background: rgba(29,29,31,0.88); color: white;
      padding: 6px 14px; border-radius: 999px;
      font: 500 12px -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
      backdrop-filter: blur(10px);
      pointer-events: auto;
    }
    .rec-dot { width: 8px; height: 8px; border-radius: 50%; background: #ff3b30;
               animation: rec-pulse 1.4s ease-in-out infinite; }
    @keyframes rec-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
    .autoplay-time { font-family: ui-monospace, "SF Mono", Menlo, monospace; opacity: 0.7; }
    .autoplay-skip { appearance: none; border: 1px solid rgba(255,255,255,0.3);
                      background: transparent; color: white;
                      padding: 3px 10px; border-radius: 999px;
                      font-size: 11px; cursor: pointer; }
    .autoplay-skip:hover { background: rgba(255,255,255,0.15); }
    .autoplay-caption {
      position: absolute; left: 50%; bottom: 40px; transform: translate(-50%, 20px);
      max-width: 740px; width: calc(100% - 80px);
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

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  async function caption(title, body, holdMs = 3200) {
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
    const y = el.getBoundingClientRect().top + (document.querySelector(".view-root")?.scrollTop || 0) - offset;
    document.querySelector(".view-root")?.scrollTo({ top: y, behavior: "smooth" });
  }
  function click(selector) { document.querySelector(selector)?.click(); }
  async function waitFor(selector, timeoutMs = 6000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      const el = document.querySelector(selector);
      if (el) return el;
      await sleep(100);
    }
    return null;
  }
  async function waitForRunDone(timeoutMs = 30000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      const txt = document.getElementById("run-progress-txt") || document.getElementById("st-progress-txt");
      if (txt && txt.textContent.startsWith("done")) return true;
      await sleep(200);
    }
    return false;
  }

  document.getElementById("ap-skip").addEventListener("click", () => location.href = location.pathname);

  async function run() {
    await waitFor(".side-link");
    await sleep(400);
    const modal = document.getElementById("intro-modal");
    if (modal && modal.classList.contains("open")) {
      document.getElementById("intro-skip")?.click();
      await sleep(300);
    }

    // ===== 1. HERO (0:00-0:12)
    await nav("#/overview");
    await waitFor(".banner");
    ring(".banner");
    await caption(
      "FinFabric — decentralized KYC for Indian banks",
      "Onboard once. Share across banks. Prove one field. Revoke a whole book. <em>Zero PII on-chain</em>. Built for the IDBI hackathon.",
      4200
    );
    scrollTo(".kpi-grid");
    ring(".kpi-grid");
    await caption("Escape rate: 0.00%", "The only fraud metric that matters — wrong fields auto-accepted onto the chain — bounded at zero across every scenario.", 3800);
    hideCaption();

    // ===== 2. SCENARIOS — rapid montage (0:12-0:50)
    await nav("#/scenarios");
    await waitFor(".scen-grid");
    await sleep(300);
    ring(".scen-grid");
    await caption("Six banking-native scenarios", "Baseline · fine-tuned · adversarial · blown MRZ · correlated engines · high-volume batch. Every knob editable.", 3500);

    // Quick highlight tour of scenario cards
    for (const key of ["fine_tuned", "date_swap_adversarial", "mrz_blown_out"]) {
      ring(`.scen-card[data-key="${key}"]`);
      await sleep(1000);
    }

    // Run baseline fast (n=10, pace=80ms)
    click('.scen-card[data-key="baseline"]');
    await sleep(300);
    const pace = document.getElementById("scen-pace"); if (pace) pace.value = 80;
    const n = document.getElementById("scen-n"); if (n) n.value = 10;
    click("#scen-run");
    await sleep(400);
    ring("#pipeline-svg");
    await caption(
      "Live pipeline · 6 stages",
      "Capture → Extract (OCR × 2 + MRZ) → Gate (5 signals) → Adjudicate → Commit (Merkle) → Anchor. Nodes pulse in sync.",
      4000
    );
    ring(".doc-panel");
    await caption(
      "Data transparency",
      "Every customer: ground truth vs both OCR reads vs MRZ evidence. See exactly why each field passed or was flagged.",
      4200
    );
    hideCaption();
    await waitForRunDone();
    scrollTo("#run-anchor-card");
    ring("#run-anchor-card");
    await caption("One tx · $0.0002", "The whole batch anchored to Base Sepolia in a single transaction. Same 32 bytes on-chain for 10 or 10,000 customers.", 4000);
    hideCaption();

    // ===== 3. STUDIO — end-user setup (0:50-1:10)
    await nav("#/studio");
    await waitFor(".studio-grid");
    await sleep(300);
    ring(".studio-grid");
    await caption(
      "Studio — judges &amp; end users build their own",
      "Plain-language sliders. Configure OCR quality, phone photo quality, attack rate, engine correlation. Save presets, run live.",
      4200
    );
    // Wiggle the CER slider to show it's interactive
    const cerSlider = document.getElementById("st-cer");
    if (cerSlider) {
      cerSlider.value = 0.1; cerSlider.dispatchEvent(new Event("input"));
      await sleep(700);
      cerSlider.value = 0.6; cerSlider.dispatchEvent(new Event("input"));
      await sleep(700);
    }
    ring("#st-preview");
    await caption("Live prediction", "As you drag, the console projects what the run will do — before you spend a byte of gas.", 3500);
    hideCaption();

    // ===== 4. CREDENTIALS drill-down + verify + tamper + proof (1:10-1:32)
    await nav("#/credentials");
    await waitFor("#cred-table tbody tr");
    await sleep(300);
    document.querySelector("#cred-table tbody tr")?.click();
    await waitFor("#drawer.open");
    await sleep(400);
    ring(".drawer.open .drawer-fields");
    await caption("Per-field signals", "Ten fields, each with five signal glyphs. Every on-chain field carries five green.", 3800);
    click("#drawer-verify"); await sleep(1000);
    ring("#drawer-verify-out");
    await caption("Selective disclosure", "Reveal two fields. Verified against the on-chain root. Other eight — not sent, not hashed.", 3800);
    click("#drawer-tamper"); await sleep(900);
    ring("#drawer-verify-out");
    await caption("Try to lie · rejected", "Tampered DOB — Merkle path breaks. Cryptographic integrity, not a policy check.", 3500);
    const proofSel = document.getElementById("drawer-proof-field");
    if (proofSel?.querySelector('option[value="date_of_birth"]')) proofSel.value = "date_of_birth";
    click("#drawer-proof-load");
    await sleep(700);
    scrollTo("#drawer-proof-view", 40);
    ring("#drawer-proof-view");
    await caption("The proof walk", "Leaf preimage · hash chain · credential root · epoch tree · on-chain anchor.", 4000);
    click("#drawer-close");
    await sleep(300);
    hideCaption();

    // ===== 5. AI FEATURES rapid tour (1:32-1:50)
    await nav("#/adjudicator");
    await waitFor(".adj-form");
    await sleep(300);
    ring(".adj-form");
    click("#run-adj");
    await caption("VLM adjudicator · live Gemini", "Third opinion for the ~5% the gate can't resolve. Uncorrelated with OCR errors by design.", 3500);
    await sleep(2500);
    ring("#adj-result");
    await caption("Chose the plausible value", "Real API call to Gemini 2.5 Flash. Human officer only if it also abstains.", 3000);

    await nav("#/audit");
    await waitFor("#audit-report");
    await sleep(300);
    click("#run-report");
    ring(".card:has(#audit-report)");
    await caption("Compliance report", "Gemini reads live state — every epoch, anchor, revocation — and writes an RBI-ready audit note.", 4000);
    await sleep(4000);

    click("#asst-fab");
    await sleep(500);
    ring(".asst-panel.open");
    await caption("Assistant on every screen", "Keyboard ⌘/. Grounded in the current state. Ask judges' questions live.", 3500);
    click("#asst-close");
    await sleep(200);

    // ===== 6. CLOSE (1:50-1:58)
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
