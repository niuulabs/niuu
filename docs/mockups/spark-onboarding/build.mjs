// Generates the Spark first-launch wizard artboards (.dc.html) for the design canvas.
// Run: node docs/mockups/spark-onboarding/build.mjs
// Visual vocabulary lifted from web-next/packages/design-tokens/src/tokens.css,
// packages/ui/src/primitives/form-control.css, plugin-volundr LaunchWizardPrimitives.tsx
// and plugin-login LoginPage.css.
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = dirname(fileURLToPath(import.meta.url));

const T = {
  bg: '#09090b',
  bg2: '#18181b',
  bg3: '#27272a',
  bg4: '#3f3f46',
  text: '#fafafa',
  text2: '#a1a1aa',
  muted: '#71717a',
  faint: '#52525b',
  border: '#3f3f46',
  borderSubtle: '#27272a',
  brand: '#38bdf8',
  brand300: '#bae6fd',
  brand400: '#7dd3fc',
  brand600: '#0ea5e9',
  ok: '#10b981',
  warn: '#f59e0b',
  critical: '#ef4444',
  criticalFg: '#fca5a5',
  violet: '#8b7cf0',
  spring: '#76b900',
  teal: '#2dd4bf',
};

const STEPS = [
  ['welcome', 'Welcome'],
  ['system', 'System check'],
  ['model', 'Local model'],
  ['providers', 'AI providers'],
  ['git', 'Git'],
  ['tracker', 'Tickets'],
  ['runtime', 'Runtime & access'],
  ['launch', 'Launch'],
];

/* ── Icons: stroke-based, 20px grid ─────────────────────────────────────── */
const svg = (body, size = 20, extra = '') =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${body}</svg>`;
const I = {
  check: (s) => svg('<path d="M5 12.5l4.5 4.5L19 7.5"></path>', s),
  x: (s) => svg('<path d="M6 6l12 12M18 6L6 18"></path>', s),
  alert: (s) => svg('<path d="M12 9v4M12 17h.01"></path><path d="M10.3 3.9L2.5 17.5A2 2 0 004.2 20.5h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"></path>', s),
  arrow: (s) => svg('<path d="M5 12h14M13 6l6 6-6 6"></path>', s),
  back: (s) => svg('<path d="M19 12H5M11 18l-6-6 6-6"></path>', s),
  gpu: (s) => svg('<rect x="3" y="6" width="18" height="12" rx="2"></rect><path d="M7 10h4M7 14h6M15 10h2"></path>', s),
  disk: (s) => svg('<circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="2.5"></circle><path d="M12 3v6"></path>', s),
  box: (s) => svg('<path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"></path><path d="M3 8l9 5 9-5M12 13v8"></path>', s),
  globe: (s) => svg('<circle cx="12" cy="12" r="9"></circle><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18"></path>', s),
  key: (s) => svg('<circle cx="8" cy="14" r="4"></circle><path d="M11 11l9-9M16 6l3 3M13 9l3 3"></path>', s),
  lock: (s) => svg('<rect x="4" y="11" width="16" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 018 0v4"></path>', s),
  branch: (s) => svg('<circle cx="6" cy="5" r="2.5"></circle><circle cx="6" cy="19" r="2.5"></circle><circle cx="18" cy="9" r="2.5"></circle><path d="M6 7.5v9M18 11.5a6 6 0 01-6 6h-2"></path>', s),
  ticket: (s) => svg('<path d="M4 8a2 2 0 012-2h12a2 2 0 012 2v2a2 2 0 000 4v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2a2 2 0 000-4V8z"></path><path d="M10 6v12"></path>', s),
  shield: (s) => svg('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"></path><path d="M9 12l2 2 4-4"></path>', s),
  play: (s) => svg('<circle cx="12" cy="12" r="9"></circle><path d="M10 8.5v7l6-3.5-6-3.5z"></path>', s),
  spark: (s) => svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"></path>', s),
  folder: (s) => svg('<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"></path>', s),
  user: (s) => svg('<circle cx="12" cy="8" r="4"></circle><path d="M4 21a8 8 0 0116 0"></path>', s),
  terminal: (s) => svg('<rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M7 9l3 3-3 3M12 15h5"></path>', s),
  copy: (s) => svg('<rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15V5a2 2 0 012-2h10"></path>', s),
  external: (s) => svg('<path d="M14 4h6v6M20 4l-9 9"></path><path d="M19 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1h5"></path>', s),
  cloud: (s) => svg('<path d="M7 18a4 4 0 01-.6-7.95A6 6 0 0118 9a4.5 4.5 0 01-.5 9H7z"></path>', s),
  net: (s) => svg('<circle cx="12" cy="5" r="2.5"></circle><circle cx="5" cy="19" r="2.5"></circle><circle cx="19" cy="19" r="2.5"></circle><path d="M12 7.5v5M12 12.5l-5.5 4.5M12 12.5l5.5 4.5"></path>', s),
};

/* Brand mark: the favicon's double-N stroke (apps/niuu/public/favicon.svg). */
const mark = (size = 28) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 56 56" fill="none" aria-hidden="true"><g stroke="${T.brand}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M10 44V12L28 38V14"></path><path d="M46 44V16L28 42V14" opacity="0.85"></path><circle cx="28" cy="28" r="2.1" fill="${T.brand}" stroke="none"></circle></g></svg>`;

/* ── Shared CSS ────────────────────────────────────────────────────────── */
const CSS = `
  body { margin: 0; background: ${T.bg}; color: ${T.text}; font-family: 'Inter', system-ui, -apple-system, sans-serif; font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
  a { color: ${T.brand300}; text-decoration: none; } a:hover { color: ${T.brand}; }
  .mono { font-family: 'JetBrains Mono', 'JetBrainsMono NF', ui-monospace, monospace; }
  .kicker { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; letter-spacing: 0.42em; text-transform: uppercase; color: ${T.brand300}; }
  .label { font-size: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.07em; color: ${T.muted}; }
  .h1 { font-size: 30px; font-weight: 700; line-height: 1.2; letter-spacing: -0.025em; color: ${T.text}; margin: 0; }
  .h2 { font-size: 20px; font-weight: 600; line-height: 1.3; color: ${T.text}; margin: 0; }
  .h3 { font-size: 14px; font-weight: 500; color: ${T.text}; margin: 0; }
  .lede { font-size: 16px; line-height: 1.6; color: ${T.text2}; margin: 0; }
  .muted { color: ${T.muted}; } .faint { color: ${T.faint}; } .sec { color: ${T.text2}; }
  .xs { font-size: 12px; line-height: 1.4; }

  .card { border: 1px solid ${T.borderSubtle}; background: ${T.bg2}; border-radius: 12px; padding: 16px; }
  .card-head { border-bottom: 1px solid ${T.borderSubtle}; padding-bottom: 12px; margin-bottom: 16px; }
  .panel { border: 1px solid ${T.borderSubtle}; background: ${T.bg}; border-radius: 8px; padding: 16px; }

  .input { display: flex; align-items: center; box-sizing: border-box; width: 100%; padding: 8px 12px; background: ${T.bg2}; border: 1px solid ${T.border}; border-radius: 8px; font-size: 14px; line-height: 1.5; color: ${T.text}; }
  .input.ph { color: ${T.muted}; }
  .field { display: flex; flex-direction: column; gap: 4px; }
  .field-label { font-size: 14px; font-weight: 500; line-height: 1.4; color: ${T.text}; }
  .hint { font-size: 12px; line-height: 1.4; color: ${T.muted}; }

  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; box-sizing: border-box; padding: 8px 12px; border-radius: 8px; border: 1px solid ${T.borderSubtle}; background: ${T.bg}; color: ${T.text}; font-size: 12px; font-weight: 500; line-height: 1.5; white-space: nowrap; }
  .btn.muted { background: ${T.bg2}; }
  .btn.ghost { border-color: transparent; color: ${T.text2}; }
  .cta { display: inline-flex; align-items: center; justify-content: center; gap: 10px; min-height: 46px; padding: 12px 24px; border-radius: 9999px; background: ${T.brand}; color: ${T.bg}; font-size: 14px; font-weight: 600; box-shadow: 0 0 36px rgba(56,189,248,0.5); white-space: nowrap; }
  .cta.disabled { opacity: 0.45; box-shadow: none; }

  .chip { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 9999px; border: 1px solid ${T.borderSubtle}; background: ${T.bg2}; font-size: 12px; color: ${T.text2}; white-space: nowrap; }
  .chip.ok { color: ${T.ok}; border-color: rgba(16,185,129,0.35); background: rgba(16,185,129,0.12); }
  .chip.warn { color: ${T.warn}; border-color: rgba(245,158,11,0.35); background: rgba(245,158,11,0.12); }
  .chip.brand { color: ${T.brand300}; border-color: rgba(56,189,248,0.35); background: rgba(56,189,248,0.10); }
  .chip.spring { color: ${T.spring}; border-color: rgba(118,185,0,0.35); background: rgba(118,185,0,0.10); }
  .chip.violet { color: ${T.violet}; border-color: rgba(139,124,240,0.35); background: rgba(139,124,240,0.12); }

  .dot { width: 8px; height: 8px; border-radius: 9999px; flex-shrink: 0; }

  .option { position: relative; display: flex; gap: 12px; align-items: flex-start; min-height: 44px; border: 1px solid ${T.borderSubtle}; border-radius: 8px; background: ${T.bg}; padding: 12px 14px; }
  .option.checked { border-color: rgba(56,189,248,0.6); background: rgba(56,189,248,0.10); box-shadow: inset 0 1px 0 rgba(255,255,255,0.06); }
  .radio { width: 18px; height: 18px; flex-shrink: 0; margin-top: 1px; border-radius: 9999px; border: 1px solid rgba(56,189,248,0.38); background: rgba(24,24,27,0.78); box-sizing: border-box; }
  .option.checked .radio { border: 5px solid ${T.brand}; background: ${T.bg}; }
  .checkbox { width: 18px; height: 18px; flex-shrink: 0; margin-top: 1px; border-radius: 5px; border: 1px solid rgba(56,189,248,0.38); background: rgba(24,24,27,0.78); box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; color: ${T.bg}; }
  .checkbox.on { background: ${T.brand}; border-color: ${T.brand}; }

  .row { display: flex; align-items: center; gap: 12px; }
  .stack { display: flex; flex-direction: column; }
  .code { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; background: ${T.bg}; border: 1px solid ${T.borderSubtle}; border-radius: 6px; padding: 8px 10px; color: ${T.text2}; }
  .meter { height: 8px; border-radius: 9999px; background: ${T.bg3}; overflow: hidden; display: flex; }

  /* wizard chrome */
  .rail { width: 300px; flex-shrink: 0; box-sizing: border-box; background: ${T.bg2}; border-right: 1px solid ${T.borderSubtle}; display: flex; flex-direction: column; padding: 28px 24px; }
  .step { display: flex; align-items: center; gap: 12px; height: 40px; }
  .step-n { width: 24px; height: 24px; border-radius: 9999px; display: inline-flex; align-items: center; justify-content: center; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; box-sizing: border-box; flex-shrink: 0; }
  .step-n.done { background: ${T.brand}; color: ${T.bg}; }
  .step-n.now { border: 2px solid ${T.brand}; color: ${T.brand}; }
  .step-n.todo { border: 1px solid ${T.borderSubtle}; color: ${T.faint}; }
  .step-l { font-size: 13px; color: ${T.faint}; }
  .step-l.now { color: ${T.text}; font-weight: 500; } .step-l.done { color: ${T.text2}; }
  .step-line { width: 1px; height: 12px; background: ${T.borderSubtle}; margin-left: 11.5px; }
  .step-line.done { background: ${T.brand}; }
  .main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .content { flex: 1; overflow: hidden; padding: 36px 64px 20px; }
  .col { width: 100%; max-width: 820px; display: flex; flex-direction: column; gap: 20px; }
  .footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 64px 28px; border-top: 1px solid ${T.borderSubtle}; }
  table { border-collapse: collapse; width: 100%; } td { padding: 0; }
`;

const HEAD = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap">
  <style>${CSS}</style>
</helmet>`;
const TAIL = `</x-dc>
</body>
</html>
`;

/* ── Wizard chrome ─────────────────────────────────────────────────────── */
function rail(currentId) {
  const idx = STEPS.findIndex(([id]) => id === currentId);
  const items = STEPS.map(([id, label], i) => {
    const state = i < idx ? 'done' : i === idx ? 'now' : 'todo';
    const n = state === 'done' ? I.check(13) : String(i + 1);
    const line = i < STEPS.length - 1 ? `<div class="step-line ${i < idx ? 'done' : ''}"></div>` : '';
    return `<div class="step"><span class="step-n ${state}">${n}</span><span class="step-l ${state}">${label}</span></div>${line}`;
  }).join('');
  return `<aside class="rail">
  <div class="row" style="gap: 10px; margin-bottom: 6px;">${mark(28)}<span style="font-weight: 600; font-size: 15px; letter-spacing: -0.01em;">Niuu</span></div>
  <div class="kicker" style="font-size: 10px; margin-bottom: 36px;">first launch</div>
  <div class="stack">${items}</div>
  <div style="flex: 1;"></div>
  <div class="stack" style="gap: 10px;">
    <div class="row" style="gap: 8px;"><span class="chip spring">${I.spark(12)} DGX Spark · GB10</span></div>
    <div class="xs muted mono">spark.local:8080 · niuu 1.4.0</div>
    <a href="#" class="xs">Setup guide ${I.external(12)}</a>
  </div>
</aside>`;
}

function shell({ id, title, lede, body, back = 'Back', next = 'Continue', nextDisabled = false, extraFooter = '' }) {
  const idx = STEPS.findIndex(([s]) => s === id);
  return `${HEAD}
<div style="width: 1440px; height: 960px; display: flex; background: ${T.bg}; overflow: hidden;">
${rail(id)}
<main class="main">
  <div class="content">
    <div class="col">
      <div class="stack" style="gap: 8px;">
        <div class="label">Step ${idx + 1} of ${STEPS.length}</div>
        <h1 class="h1">${title}</h1>
        <p class="lede">${lede}</p>
      </div>
      ${body}
    </div>
  </div>
  <div class="footer">
    <span class="btn ghost">${I.back(14)} ${back}</span>
    <div class="row" style="gap: 12px;">${extraFooter}<span class="cta ${nextDisabled ? 'disabled' : ''}">${next} ${I.arrow(16)}</span></div>
  </div>
</main>
</div>
${TAIL}`;
}

/* helpers */
const chip = (cls, text, icon = '') => `<span class="chip ${cls}">${icon}${text}</span>`;
const field = (label, value, { ph = false, hint = '', mono = false, trailing = '' } = {}) =>
  `<div class="field"><span class="field-label">${label}</span><div class="row" style="gap: 8px;"><div class="input ${ph ? 'ph' : ''} ${mono ? 'mono' : ''}" style="flex: 1;">${value}</div>${trailing}</div>${hint ? `<span class="hint">${hint}</span>` : ''}</div>`;
const option = (checked, title, desc, right = '', kind = 'radio') =>
  `<div class="option ${checked ? 'checked' : ''}"><span class="${kind} ${checked && kind === 'checkbox' ? 'on' : ''}">${checked && kind === 'checkbox' ? I.check(12) : ''}</span><div class="stack" style="flex: 1; gap: 2px;"><div class="row" style="justify-content: space-between;"><span class="h3">${title}</span>${right}</div><span class="xs sec">${desc}</span></div></div>`;
const statusRow = (state, title, detail, action = '') => {
  const color = state === 'ok' ? T.ok : state === 'warn' ? T.warn : state === 'run' ? T.brand : state === 'fail' ? T.critical : T.faint;
  const icon = state === 'ok' ? I.check(14) : state === 'warn' ? I.alert(14) : state === 'fail' ? I.x(14) : '';
  return `<div class="row" style="gap: 14px; padding: 10px 0; border-bottom: 1px solid ${T.borderSubtle};">
    <span style="width: 22px; height: 22px; border-radius: 9999px; display: inline-flex; align-items: center; justify-content: center; color: ${state === 'todo' ? T.faint : T.bg}; background: ${state === 'todo' ? 'transparent' : color}; border: 1px solid ${state === 'todo' ? T.border : color}; flex-shrink: 0; box-sizing: border-box;">${icon}</span>
    <div class="stack" style="flex: 1; gap: 1px;"><span class="h3">${title}</span><span class="xs sec">${detail}</span></div>${action}</div>`;
};

/* ── Artboards ─────────────────────────────────────────────────────────── */

const Terminal = `${HEAD}
<div style="width: 1100px; height: 620px; box-sizing: border-box; background: ${T.bg}; padding: 48px 56px; display: flex; flex-direction: column; gap: 24px;">
  <div class="stack" style="gap: 6px;">
    <div class="kicker">step 0 · the only command</div>
    <h1 class="h1" style="font-size: 26px;">One command on a clean Spark</h1>
    <p class="lede" style="font-size: 14px;">The installer fetches the CLI, checks Docker and the GPU, pulls the stack, and hands off to the browser wizard. Nothing is asked in the terminal.</p>
  </div>
  <div style="border: 1px solid ${T.borderSubtle}; border-radius: 12px; background: ${T.bg2}; overflow: hidden;">
    <div class="row" style="height: 36px; padding: 0 14px; border-bottom: 1px solid ${T.borderSubtle}; gap: 8px;">
      <span class="dot" style="background: ${T.bg4};"></span><span class="dot" style="background: ${T.bg4};"></span><span class="dot" style="background: ${T.bg4};"></span>
      <span class="xs mono muted" style="margin-left: 8px;">jozef@spark — ssh</span>
    </div>
    <pre class="mono" style="margin: 0; padding: 20px 22px; font-size: 13px; line-height: 1.7; color: ${T.text2}; white-space: pre;"><span style="color: ${T.muted};">$</span> <span style="color: ${T.text};">curl -fsSL https://get.niuu.ai | sh</span>

<span style="color: ${T.brand};">niuu</span> 1.4.0 · linux/arm64
<span style="color: ${T.ok};">✓</span> Docker Engine 27.3            <span style="color: ${T.ok};">✓</span> NVIDIA Container Toolkit 1.17
<span style="color: ${T.ok};">✓</span> GPU  GB10 · 128 GB unified     <span style="color: ${T.ok};">✓</span> 3.2 TB free on /var/lib/niuu
<span style="color: ${T.ok};">✓</span> Ports 8080 8088 9100-9199 free <span style="color: ${T.warn};">!</span> user not in docker group (wizard can fix)

Pulling niuu stack  <span style="color: ${T.brand};">━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</span> 7/7 images
Starting postgres, volundr, skuld, ting, bifrost, web   <span style="color: ${T.ok};">ready in 14s</span>

<span style="color: ${T.text};">Open</span> <span style="color: ${T.brand300}; text-decoration: underline;">http://spark.local:8080/setup</span> <span style="color: ${T.text};">to finish setup.</span>
<span style="color: ${T.muted};">(this page is only reachable from your network; sign-in is off until you turn it on)</span></pre>
  </div>
  <div class="row" style="gap: 24px;">
    <div class="row" style="gap: 8px;">${I.terminal(16)}<span class="xs sec">Idempotent: rerun <span class="mono">niuu up</span> any time. <span class="mono">niuu doctor</span> re-runs the checks.</span></div>
  </div>
</div>
${TAIL}`;

const Welcome = `${HEAD}
<div style="width: 1440px; height: 900px; box-sizing: border-box; background: linear-gradient(180deg, ${T.bg} 0%, rgba(56,189,248,0.12) 68%, ${T.bg} 74%); display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 64px; position: relative; overflow: hidden;">
  <div class="xs mono muted" style="position: absolute; top: 18px; left: 22px;">niuu 1.4.0 · spark.local</div>
  <div style="width: 720px; display: flex; flex-direction: column; align-items: center; gap: 22px; text-align: center;">
    <div class="kicker">níu · first launch</div>
    <div style="color: ${T.brand}; filter: drop-shadow(0 0 14px rgba(56,189,248,0.6));">${mark(72)}</div>
    <h1 class="h1" style="font-size: 38px;">Let's set up Niuu on this Spark.</h1>
    <p class="lede" style="max-width: 560px;">About ten minutes. Everything you enter stays on this machine, encrypted, and can be changed later in Settings.</p>
    <div class="row" style="gap: 8px; flex-wrap: wrap; justify-content: center;">
      ${chip('spring', 'NVIDIA DGX Spark', I.spark(12))}${chip('', 'GB10 Grace Blackwell')}${chip('', '128 GB unified memory')}${chip('', 'Ubuntu 24.04 · arm64')}${chip('', 'Docker 27.3')}
    </div>
    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; width: 100%; margin-top: 8px;">
      ${[['1', 'Check the system', 'Docker, GPU, disk, ports.'], ['2', 'Pick a local model', 'Served on this Spark with vLLM.'], ['3', 'Connect your tools', 'Claude, Codex, Grok, Git, Linear.'], ['4', 'Launch', 'Then run your first session.']]
        .map(([n, t, d]) => `<div class="panel" style="text-align: left; background: rgba(24,24,27,0.72); display: flex; flex-direction: column; gap: 6px;"><span class="mono" style="color: ${T.brand}; font-size: 12px;">0${n}</span><span class="h3">${t}</span><span class="xs sec">${d}</span></div>`).join('')}
    </div>
    <div class="stack" style="gap: 12px; align-items: center; margin-top: 8px;">
      <span class="cta" style="width: 320px;">Begin setup ${I.arrow(16)}</span>
      <a href="#" class="xs">I have a config file from another Niuu</a>
    </div>
  </div>
</div>
${TAIL}`;

const SystemCheck = shell({
  id: 'system',
  title: 'System check',
  lede: 'Niuu verified this Spark can run the whole stack. One thing needs a fix before sessions can start containers.',
  body: `
  <div class="card">
    <div class="card-head row" style="justify-content: space-between;">
      <div class="stack"><span class="h3">8 checks · 7 passed · 1 warning</span><span class="xs faint">Ran 2 s ago · re-runs automatically when fixed</span></div>
      <span class="btn muted">Re-run checks</span>
    </div>
    <div class="stack">
      ${statusRow('ok', 'Docker Engine 27.3', 'containerd 1.7 · overlay2 on /var/lib/docker')}
      ${statusRow('warn', 'Docker socket needs sudo', 'Your user is not in the <span class="mono">docker</span> group, so sessions could not start sandboxes.', `<div class="row" style="gap: 8px;"><span class="code">sudo usermod -aG docker $USER</span><span class="btn muted">${I.copy(13)} Copy</span><span class="btn" style="border-color: rgba(56,189,248,0.5); color: ${T.brand300};">Fix for me</span></div>`)}
      ${statusRow('ok', 'NVIDIA Container Toolkit 1.17', 'GPU is visible inside containers · driver 570.86 · CUDA 12.8')}
      ${statusRow('ok', 'GPU: GB10 Grace Blackwell', '128 GB unified memory · 0.6 GB in use')}
      ${statusRow('ok', 'Disk', '3.2 TB free of 3.8 TB on /var/lib/niuu · models and workspaces live here')}
      ${statusRow('ok', 'Ports', '8080 (web + API), 8088 (model gateway), 9100–9199 (session channels) are free')}
      ${statusRow('ok', 'Outbound network', 'ghcr.io, huggingface.co, api.anthropic.com reachable · 940 Mbit/s')}
      ${statusRow('ok', 'Git 2.43', 'user.name and user.email are set')}
    </div>
  </div>
  <div class="row" style="gap: 10px;">${I.shield(16)}<span class="xs sec">Warnings do not block setup. Anything still failing is listed again on the Launch step.</span></div>`,
});

const LocalModel = shell({
  id: 'model',
  title: 'Run a model on this Spark?',
  lede: 'vLLM serves it here and Bifrost routes to it. Sessions, workflows and residents can then work without code leaving the box.',
  body: `
  <div style="display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 20px; align-items: start;">
    <div class="card">
      <div class="card-head"><span class="h3">Recommended for 128 GB unified memory</span><div class="xs faint" style="margin-top: 2px;">Sizes include the KV cache at 64k context. You can add more models later.</div></div>
      <div class="stack" style="gap: 10px;">
        ${option(true, 'NVIDIA Nemotron 3 Nano 30B', 'Fast agentic coder tuned by NVIDIA. Best default for sessions and residents on Spark.', `<div class="row" style="gap: 6px;">${chip('spring', 'Recommended')}${chip('ok', 'Fits · ~62 GB')}</div>`)}
        ${option(false, 'OpenAI gpt-oss-120b', 'Larger reasoning model. Slower per token, stronger on planning.', chip('ok', 'Fits · ~78 GB'))}
        ${option(false, 'Qwen3-Coder 30B-A3B', 'Lean coding model with generous headroom for long contexts.', chip('ok', 'Fits · ~24 GB'))}
        ${option(false, 'Custom Hugging Face model', 'Any vLLM-compatible repo, e.g. <span class="mono">nvidia/…</span>. Niuu checks it fits before pulling.', '')}
        ${option(false, 'Skip — cloud models only', 'You can add a local model later from Settings → Models.', '')}
      </div>
    </div>
    <div class="stack" style="gap: 16px;">
      <div class="card stack" style="gap: 12px;">
        <span class="label">Memory after this choice</span>
        <div class="meter"><span style="width: 48%; background: ${T.brand};"></span><span style="width: 6%; background: ${T.violet};"></span></div>
        <div class="stack xs" style="gap: 4px;">
          <div class="row" style="justify-content: space-between;"><span class="row" style="gap: 6px;"><span class="dot" style="background: ${T.brand};"></span><span class="sec">Nemotron 3 Nano</span></span><span class="mono">62 GB</span></div>
          <div class="row" style="justify-content: space-between;"><span class="row" style="gap: 6px;"><span class="dot" style="background: ${T.violet};"></span><span class="sec">Session sandboxes</span></span><span class="mono">~8 GB</span></div>
          <div class="row" style="justify-content: space-between;"><span class="row" style="gap: 6px;"><span class="dot" style="background: ${T.bg4};"></span><span class="sec">Free</span></span><span class="mono">58 GB</span></div>
        </div>
      </div>
      <div class="card stack" style="gap: 8px;">
        <span class="label">Download</span>
        <span class="xs sec">~62 GB from Hugging Face at your measured 940 Mbit/s: about <b style="color: ${T.text};">9 minutes</b>. It continues in the background while you finish setup.</span>
        ${field('Hugging Face token', 'hf_••••••••••••••••••', { mono: true, hint: 'Optional · needed only for gated repos' })}
      </div>
    </div>
  </div>`,
  next: 'Pull and continue',
});

const providerCard = (name, sub, body, state) => `
  <div class="card" style="display: flex; flex-direction: column; gap: 14px;">
    <div class="row" style="justify-content: space-between;">
      <div class="stack"><span class="h3">${name}</span><span class="xs faint">${sub}</span></div>${state}
    </div>${body}
  </div>`;

const Providers = shell({
  id: 'providers',
  title: 'Connect AI providers',
  lede: 'Sign in to the ones you use. Niuu stores tokens encrypted on this machine and only ever sends them to that provider.',
  body: `
  <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px;">
    ${providerCard('Anthropic · Claude', 'Claude Code sessions, Ravn judgment', `
      <div class="panel row" style="gap: 12px; border-color: rgba(16,185,129,0.35);">
        <span style="color: ${T.ok};">${I.check(18)}</span>
        <div class="stack" style="flex: 1;"><span class="h3">Signed in with your Claude subscription</span><span class="xs sec">jozef@… · Max plan · token refreshes automatically</span></div>
        <span class="btn ghost">Sign out</span>
      </div>
      <div class="xs faint">Or use an API key instead · <a href="#">switch</a></div>`, chip('ok', 'Connected', I.check(12)))}
    ${providerCard('OpenAI · Codex', 'Codex sessions', `
      <div class="panel stack" style="gap: 10px; border-color: rgba(56,189,248,0.35);">
        <span class="xs sec">Open <a href="#">chatgpt.com/device</a> and enter this code:</span>
        <div class="row" style="gap: 10px;"><span class="mono" style="font-size: 26px; letter-spacing: 0.18em; font-weight: 600; color: ${T.text};">GHXK-2PLQ</span><span class="btn muted">${I.copy(13)} Copy</span></div>
        <div class="row" style="gap: 8px;"><span class="dot" style="background: ${T.brand}; box-shadow: 0 0 8px ${T.brand};"></span><span class="xs sec">Waiting for approval · expires in 12:40</span></div>
      </div>
      <div class="xs faint">Or paste an API key · <a href="#">switch</a></div>`, chip('brand', 'Waiting'))}
    ${providerCard('xAI · Grok', 'Grok models through the model gateway', `
      ${field('API key', 'xai-••••••••••••••••••••••••••', { mono: true, trailing: '<span class="btn muted">Test key</span>' })}
      <div class="row xs" style="gap: 6px; color: ${T.ok};">${I.check(13)} Key works · grok-4 available</div>`, chip('ok', 'Connected', I.check(12)))}
    ${providerCard('Local · vLLM on this Spark', 'Nemotron 3 Nano 30B', `
      <div class="row" style="gap: 8px;"><span class="dot" style="background: ${T.brand}; box-shadow: 0 0 8px ${T.brand};"></span><span class="xs sec">Pulling · 41 of 62 GB · ready in ~3 min</span></div>
      <div class="meter"><span style="width: 66%; background: ${T.brand};"></span></div>
      <div class="xs faint">Configured on the previous step. No key needed.</div>`, chip('brand', 'Pulling'))}
  </div>
  <div class="card row" style="gap: 16px; justify-content: space-between;">
    <div class="stack"><span class="h3">Default model for new sessions</span><span class="xs faint">Every session can override this. No silent fallbacks: if a provider is down, the session says so.</span></div>
    <div class="input" style="width: 300px; justify-content: space-between;"><span>Claude Sonnet 5</span><span class="muted">▾</span></div>
  </div>
  <div class="row" style="gap: 10px;">${I.lock(16)}<span class="xs sec">Encrypted with a key generated on this Spark at <span class="mono">/var/lib/niuu/credentials</span>. Add Google, Mistral or any OpenAI-compatible endpoint later in Settings → Models.</span></div>`,
});

const Git = shell({
  id: 'git',
  title: 'Connect Git',
  lede: 'Sessions clone from and push to these. Local folders on this Spark are mounted straight into session sandboxes.',
  body: `
  <div class="card stack" style="gap: 14px;">
    <div class="row" style="justify-content: space-between;">
      <div class="stack"><span class="h3">GitHub</span><span class="xs faint">GitHub App is recommended: scoped to the repos you pick, no long-lived personal token.</span></div>${chip('ok', 'Connected', I.check(12))}
    </div>
    <div class="panel row" style="gap: 12px; border-color: rgba(16,185,129,0.35);">
      <span style="color: ${T.ok};">${I.check(18)}</span>
      <div class="stack" style="flex: 1;"><span class="h3">Niuu app installed on niuulabs</span><span class="xs sec">42 repositories · installation #18274 · can read code, open pull requests, comment</span></div>
      <span class="btn ghost">Manage on GitHub ${I.external(12)}</span>
    </div>
    <div class="xs faint">Or use a personal access token · <a href="#">switch</a></div>
  </div>
  <div class="card stack" style="gap: 14px;">
    <div class="row" style="justify-content: space-between;"><div class="stack"><span class="h3">GitLab</span><span class="xs faint">Self-hosted or gitlab.com.</span></div>${chip('', 'Not connected')}</div>
    <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px;">
      ${field('Base URL', 'https://gitlab.com', { ph: true, mono: true })}
      ${field('Access token', 'glpat-…', { ph: true, mono: true, trailing: '<span class="btn muted">Test</span>' })}
    </div>
  </div>
  <div class="card stack" style="gap: 14px;">
    <div class="row" style="justify-content: space-between;"><div class="stack"><span class="h3">Local repositories on this Spark</span><span class="xs faint">Mounted read-write into sandboxes. Good for code that never leaves the box.</span></div>${chip('brand', '1 folder')}</div>
    <div class="row" style="gap: 8px;">
      <div class="input mono" style="flex: 1;">${I.folder(14)}&nbsp; /home/jozef/git</div><span class="btn muted">Browse</span><span class="btn ghost">${I.x(13)}</span>
    </div>
    <div class="row" style="gap: 8px;"><span class="btn muted">Add folder</span><span class="xs faint">17 repositories found · niuu, laevateinn, ravn-bench, …</span></div>
  </div>`,
});

const Tracker = shell({
  id: 'tracker',
  title: 'Where does work come from?',
  lede: 'Ting turns issues into sagas and dispatches them. Pick one tracker now; more can be added later.',
  body: `
  <div class="stack" style="gap: 10px;">
    ${option(true, 'Linear', 'Sagas are created from issues in the teams you choose. Status and comments flow back.', chip('ok', 'Connected', I.check(12)))}
    <div class="card" style="margin-left: 30px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: end;">
      <div class="panel row" style="gap: 12px; grid-column: span 2; border-color: rgba(16,185,129,0.35);">
        <span style="color: ${T.ok};">${I.check(18)}</span>
        <div class="stack" style="flex: 1;"><span class="h3">Authorized with Linear</span><span class="xs sec">Workspace Niuu Labs · as Jozef van Eenbergen · read and write issues</span></div>
        <span class="btn ghost">Disconnect</span>
      </div>
      <div class="field"><span class="field-label">Teams to watch</span><div class="input" style="justify-content: space-between;"><span class="row" style="gap: 6px;">${chip('brand', 'NIU · Niuu')}${chip('brand', 'IVA · Ivaldi')}</span><span class="muted">▾</span></div></div>
      <div class="field"><span class="field-label">Dispatch when</span><div class="input" style="justify-content: space-between;"><span>Issue is moved to <b>Todo</b> and labeled <span class="mono">niuu</span></span><span class="muted">▾</span></div><span class="hint">Nothing runs on its own until you approve the first plan.</span></div>
    </div>
    ${option(false, 'GitHub Issues', 'Uses the GitHub App you installed. Issues labeled <span class="mono">niuu</span> become sagas.', chip('', 'Ready to enable'))}
    ${option(false, 'Jira', 'Cloud or Data Center. Needs a site URL and an API token.', '')}
    ${option(false, 'Niuu built-in tracker', 'No external service. Create and plan work inside Niuu.', chip('', 'Always available'))}
  </div>`,
});

const Runtime = shell({
  id: 'runtime',
  title: 'Runtime and access',
  lede: 'Where sessions execute, where their files live, and who can reach this Niuu.',
  body: `
  <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: start;">
    <div class="card stack" style="gap: 10px;">
      <div class="card-head" style="margin-bottom: 2px;"><span class="h3">Where sessions run</span><div class="xs faint" style="margin-top: 2px;">Each session gets its own runtime with the GPU, your Git credentials and the workspace mounted in.</div></div>
      ${option(true, 'OpenShell sandbox', 'Isolated NVIDIA OpenShell sandbox per session with a network policy. Credentials are injected per session, never written to disk inside it.', chip('spring', 'Recommended'))}
      ${option(false, 'Docker container', 'One container per session on the host daemon. Simpler, weaker isolation.', '')}
      ${option(false, 'Host process', 'Runs Claude and Codex directly on this machine. No isolation; single trusted user only.', chip('warn', 'Not isolated', I.alert(12)))}
      <div class="stack" style="gap: 12px; padding-top: 6px;">
        ${field('Workspaces directory', '/var/lib/niuu/workspaces', { mono: true, hint: '3.2 TB free · clones, build caches and session scratch live here' })}
        ${option(true, 'Keep workspaces after a session ends', 'Reopen later or let a resident continue the work.', '', 'checkbox')}
      </div>
    </div>
    <div class="card stack" style="gap: 10px;">
      <div class="card-head" style="margin-bottom: 2px;"><span class="h3">Who can reach this Niuu</span><div class="xs faint" style="margin-top: 2px;">Sign-in is off by default. Turn it on later in Settings → Access.</div></div>
      ${option(false, 'Only this machine', '<span class="mono">localhost:8080</span>', '')}
      ${option(true, 'Your local network', '<span class="mono">http://spark.local:8080</span> · anyone on the LAN', '')}
      ${option(false, 'Public, behind sign-in', 'Reverse proxy with HTTPS and an identity provider (OIDC). Set up later in Settings → Access.', '')}
      <div class="row xs" style="gap: 8px; color: ${T.warn}; padding-top: 4px;">${I.alert(14)}<span>Anyone who can open spark.local:8080 can start sessions on this Spark.</span></div>
    </div>
  </div>`,
});

const Launch = shell({
  id: 'launch',
  title: 'Bringing Niuu up',
  lede: 'Review what you chose while the stack comes up. Anything here can be changed later in Settings.',
  back: 'Back',
  next: 'Open Niuu',
  nextDisabled: true,
  extraFooter: `<span class="xs sec">Ready in about 3 minutes</span>`,
  body: `
  <div style="display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 20px; align-items: start;">
    <div class="card stack" style="gap: 0;">
      <span class="label" style="margin-bottom: 6px;">Your setup</span>
      ${[['Hardware', 'DGX Spark · GB10 · 128 GB'], ['Local model', 'Nemotron 3 Nano 30B · vLLM'], ['Providers', 'Claude, Codex, Grok + local'], ['Default model', 'Claude Sonnet 5'], ['Git', 'GitHub App · niuulabs · 1 local folder'], ['Tickets', 'Linear · NIU, IVA'], ['Runtime', 'OpenShell sandbox'], ['Access', 'Local network · sign-in off']]
        .map(([k, v]) => `<div class="row" style="justify-content: space-between; padding: 9px 0; border-bottom: 1px solid ${T.borderSubtle}; gap: 12px;"><span class="xs muted" style="width: 90px; flex-shrink: 0;">${k}</span><span class="xs" style="flex: 1; color: ${T.text};">${v}</span><a href="#" class="xs">Edit</a></div>`).join('')}
    </div>
    <div class="stack" style="gap: 16px;">
      <div class="card stack" style="gap: 12px;">
        <div class="row" style="justify-content: space-between;"><span class="h3">Progress</span><span class="mono xs sec">6 of 9 · 68%</span></div>
        <div class="meter"><span style="width: 68%; background: ${T.brand};"></span></div>
        <div class="stack">
          ${statusRow('ok', 'Generated encryption key', 'Stored in /var/lib/niuu/keys · back it up from Settings → Security')}
          ${statusRow('ok', 'Wrote configuration', '/etc/niuu/config.yaml · 3 credentials encrypted')}
          ${statusRow('ok', 'Postgres 17 + migrations', '8 databases · 71 migrations applied')}
          ${statusRow('ok', 'Bifrost model gateway', '4 providers registered on :8088')}
          ${statusRow('ok', 'Forge, Ting, Skuld', 'Session lifecycle, dispatcher and session gateway are up')}
          ${statusRow('ok', 'OpenShell sandbox image', 'ghcr.io/niuulabs/openshell:1.4.0 pulled')}
          ${statusRow('run', 'Pulling Nemotron 3 Nano 30B', '41 of 62 GB · ~3 min remaining · vLLM starts when it finishes', `<span class="mono xs" style="color: ${T.brand};">66%</span>`)}
          ${statusRow('todo', 'Warm up local model', 'First request compiles kernels; takes about a minute')}
          ${statusRow('todo', 'Verify a session can start', 'Starts and stops a throwaway sandbox')}
        </div>
      </div>
    </div>
  </div>`,
});

const tutorial = (n, title, mins, lines, primary = false) => `
  <div class="card stack" style="gap: 14px; ${primary ? 'border-color: rgba(56,189,248,0.45); background: rgba(56,189,248,0.06);' : ''}">
    <div class="row" style="justify-content: space-between;"><span class="mono" style="color: ${T.brand}; font-size: 12px;">0${n}</span><span class="xs faint">${mins} min</span></div>
    <span class="h2" style="font-size: 18px;">${title}</span>
    <div class="stack xs sec" style="gap: 6px;">${lines.map((l) => `<div class="row" style="gap: 8px; align-items: flex-start;"><span class="dot" style="background: ${T.bg4}; margin-top: 6px;"></span><span>${l}</span></div>`).join('')}</div>
    <div style="flex: 1;"></div>
    <span class="${primary ? 'cta' : 'btn muted'}" style="${primary ? 'min-height: 40px; padding: 8px 18px;' : 'padding: 10px 14px; font-size: 13px;'}">${I.play(16)} Start</span>
  </div>`;

const Ready = `${HEAD}
<div style="width: 1440px; height: 900px; box-sizing: border-box; background: ${T.bg}; display: flex; flex-direction: column; padding: 48px 64px; gap: 28px;">
  <div class="row" style="justify-content: space-between;">
    <div class="row" style="gap: 10px;">${mark(28)}<span style="font-weight: 600; font-size: 15px;">Niuu</span><span class="kicker" style="font-size: 10px; margin-left: 8px;">ready</span></div>
    <div class="row" style="gap: 8px;">${chip('ok', 'Local model ready', I.check(12))}${chip('ok', '3 providers')}${chip('ok', '42 repos')}${chip('ok', 'Linear')}${chip('brand', 'spark.local:8080')}</div>
  </div>
  <div class="stack" style="gap: 8px; max-width: 760px;">
    <h1 class="h1" style="font-size: 34px;">Niuu is running on this Spark.</h1>
    <p class="lede">Three short walkthroughs run inside the real product with your own repositories. Each one leaves you with something working.</p>
  </div>
  <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; align-items: stretch;">
    ${tutorial(1, 'Run your first session', 5, ['Open a repository from niuulabs.', 'Ask Claude to fix a failing test in an isolated sandbox.', 'Watch the Forge stream, then review and merge the pull request.'], true)}
    ${tutorial(2, 'Turn a Linear issue into a workflow', 10, ['Pick an issue from NIU.', 'Ting plans it into a saga with gates you approve.', 'Runs dispatch on the local model; cloud only where you say so.'])}
    ${tutorial(3, 'Meet your first resident', 8, ['Start a Ravn that lives on this Spark and watches your repos.', 'Give it one standing job.', 'See it ask before acting, then learn from the outcome.'])}
  </div>
  <div class="row" style="justify-content: space-between; margin-top: auto;">
    <a href="#" class="xs">Skip the walkthroughs and open the dashboard</a>
    <div class="row xs faint" style="gap: 16px;"><span class="row" style="gap: 6px;">${I.terminal(13)} <span class="mono">niuu status</span></span><span class="row" style="gap: 6px;">${I.shield(13)} Turn on sign-in</span><span class="row" style="gap: 6px;">${I.key(13)} Back up encryption key</span></div>
  </div>
</div>
${TAIL}`;

/* ── Write files ────────────────────────────────────────────────────────── */
const files = {
  'Terminal.dc.html': Terminal,
  'Main.dc.html': Welcome,
  'SystemCheck.dc.html': SystemCheck,
  'LocalModel.dc.html': LocalModel,
  'Providers.dc.html': Providers,
  'Git.dc.html': Git,
  'Tracker.dc.html': Tracker,
  'Runtime.dc.html': Runtime,
  'Launch.dc.html': Launch,
  'Ready.dc.html': Ready,
};
for (const [name, html] of Object.entries(files)) writeFileSync(join(OUT, name), html);

const W = 1440, H = 960, GX = 120, GY = 160;
const col = (i) => i * (W + GX);
const row = (i) => i * (H + GY);
const canvas = {
  artboards: [
    { file: 'Terminal.dc.html', title: '0 · One command', x: 0, y: 140, w: 1100, h: 620 },
    { file: "Main.dc.html", title: "1 · Welcome", x: 1100 + GX, y: 0, w: W, h: 900 },
    { file: 'SystemCheck.dc.html', title: '2 · System check', x: col(0), y: row(1), w: W, h: H },
    { file: 'LocalModel.dc.html', title: '3 · Local model', x: col(1), y: row(1), w: W, h: H },
    { file: 'Providers.dc.html', title: '4 · AI providers', x: col(2), y: row(1), w: W, h: H },
    { file: 'Git.dc.html', title: '5 · Git', x: col(0), y: row(2), w: W, h: H },
    { file: 'Tracker.dc.html', title: '6 · Tickets', x: col(1), y: row(2), w: W, h: H },
    { file: 'Runtime.dc.html', title: '7 · Runtime & access', x: col(2), y: row(2), w: W, h: H },
    { file: 'Launch.dc.html', title: '8 · Launch', x: col(0), y: row(3), w: W, h: H },
    { file: 'Ready.dc.html', title: '9 · Ready + tutorials', x: col(1), y: row(3), w: W, h: H },
  ],
  annotations: [
    { id: 'flow', x: 0, y: -260, w: 640, text: 'Spark first-launch flow\n\nTerminal: `curl … | sh` installs the CLI, runs preflight, pulls the compose bundle, starts the stack, prints the setup URL. No terminal prompts.\n\nBrowser: /setup wizard (this canvas), 8 steps, ~10 min. Left rail = progress, right = one decision per screen. Every step has a "connected" state and a fix-it path; nothing degrades silently.\n\nAfter Launch: /ready with three in-product walkthroughs.' },
    { id: 'principles', x: 720, y: -260, w: 360, text: 'Rules baked into the copy\n\n• No fallbacks: a down provider is reported, never substituted.\n• Credentials encrypted at rest with a per-machine key; OAuth/device-code preferred over pasted tokens.\n• Sign-in off by default on LAN, with a visible warning and a later path.\n• Everything editable later in Settings; the wizard never becomes the only way.' },
    { id: 'open', x: col(2), y: row(3) + 40, w: 420, text: 'Open questions for Jozef\n\n1. Model list: show only what fits, or everything with a "does not fit" state?\n2. Should Launch also start a resident (Muninn-style) by default, or leave that to tutorial 3?\n3. Is "Host process" runtime worth offering on Spark at all?\n4. GitHub App vs PAT as the first-class path (App needs a published app on GitHub).' },
  ],
  launch: { view: 'canvas' },
};
writeFileSync(join(OUT, 'canvas.json'), JSON.stringify(canvas, null, 2) + '\n');
console.log(`wrote ${Object.keys(files).length} artboards + canvas.json to ${OUT}`);
