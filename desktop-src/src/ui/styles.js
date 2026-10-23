import { LAYERS } from '../data/layers.js'

const LAYER_VARS = LAYERS.map((l, i) => `  --hom-l${i}: var(${l.cssVar}, var(${l.fallbackVar}));`).join('\n')

// Page styles. Rendered as a <style> inside the page, so they mount and unmount with it. Every
// colour is a theme variable (or a color-mix of theme variables).
export const CSS = /* css */ `
.hom-root {
${LAYER_VARS}
  --hom-live: var(--ui-green, var(--ui-accent));
  --hom-warn: var(--ui-yellow, var(--ui-orange));
  --hom-bad: var(--ui-red, var(--ui-orange));
  --hom-demo: var(--ui-purple, var(--ui-accent));
  --hom-glass: color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 72%, transparent);
  --hom-glass-strong: color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 88%, transparent);
  --hom-line: color-mix(in oklab, var(--ui-stroke-secondary) 75%, transparent);
  --hom-line-soft: color-mix(in oklab, var(--ui-stroke-secondary) 40%, transparent);
  --hom-ease: cubic-bezier(.2, .75, .15, 1);
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  isolation: isolate;
  color: var(--ui-text-primary);
  font-family: var(--font-sans, inherit);
  font-size: 12px;
  outline: none;
  user-select: none;
}
.hom-root *, .hom-root *::before, .hom-root *::after { box-sizing: border-box; }
.hom-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; cursor: grab; }
.hom-canvas[data-hover="true"] { cursor: pointer; }
.hom-canvas:active { cursor: grabbing; }
.hom-shade {
  position: absolute; inset: 0; pointer-events: none;
  background:
    linear-gradient(to bottom, color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 55%, transparent), transparent 120px),
    linear-gradient(to top, color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 50%, transparent), transparent 110px),
    radial-gradient(ellipse 75% 70% at 50% 50%, transparent 60%, color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 45%, transparent) 100%);
}

/* Shared bits ------------------------------------------------------------------------------ */
.hom-panel {
  background: var(--hom-glass);
  backdrop-filter: blur(18px) saturate(1.25);
  -webkit-backdrop-filter: blur(18px) saturate(1.25);
  border: 1px solid var(--hom-line);
  border-radius: 12px;
  box-shadow: 0 18px 50px -28px color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 60%, var(--ui-text-primary) 4%);
}
.hom-label {
  font: 500 9.5px/1.25 var(--font-mono, ui-monospace, monospace);
  letter-spacing: .15em;
  text-transform: uppercase;
  color: var(--ui-text-tertiary);
}
.hom-mono { font-family: var(--font-mono, ui-monospace, monospace); }
.hom-num { font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }
.hom-muted { color: var(--ui-text-tertiary); }
.hom-soft { color: var(--ui-text-secondary); }
.hom-code {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 22px; height: 16px; padding: 0 4px;
  font: 600 9.5px/1 var(--font-mono, ui-monospace, monospace); letter-spacing: .04em;
  color: var(--ui-text-secondary);
  border: 1px solid var(--hom-line); border-radius: 4px;
  background: color-mix(in oklab, var(--ui-text-primary) 4%, transparent);
}
.hom-btn {
  appearance: none; border: 0; background: transparent; color: inherit; font: inherit;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 30px; min-width: 30px; padding: 0 9px; border-radius: 8px; cursor: pointer;
  color: var(--ui-text-secondary);
  transition: background .18s var(--hom-ease), color .18s var(--hom-ease), transform .18s var(--hom-ease);
}
.hom-btn:hover { background: var(--ui-control-hover-background, color-mix(in oklab, var(--ui-text-primary) 8%, transparent)); color: var(--ui-text-primary); }
.hom-btn:active { transform: scale(.96); }
.hom-btn:focus-visible, .hom-row:focus-visible, .hom-chip:focus-visible, .hom-tab:focus-visible, .hom-link:focus-visible {
  outline: 2px solid color-mix(in oklab, var(--ui-accent) 70%, transparent); outline-offset: 1px;
}
.hom-btn[aria-pressed="true"] { color: var(--ui-text-primary); background: color-mix(in oklab, var(--ui-accent) 16%, transparent); }
.hom-btn--primary {
  background: var(--ui-accent); color: var(--ui-bg-editor, var(--ui-base));
  font-weight: 600; padding: 0 14px; height: 34px;
}
.hom-btn--primary:hover { background: color-mix(in oklab, var(--ui-accent) 86%, var(--ui-text-primary)); color: var(--ui-bg-editor, var(--ui-base)); }
.hom-btn--ghost { border: 1px solid var(--hom-line); height: 34px; padding: 0 14px; }
.hom-btn .codicon { font-size: 14px; }
.hom-link {
  appearance: none; border: 0; background: none; padding: 0; font: inherit; cursor: pointer;
  color: var(--ui-text-secondary); text-decoration: underline; text-decoration-color: var(--hom-line); text-underline-offset: 2px;
}
.hom-link:hover { color: var(--ui-text-primary); text-decoration-color: currentColor; }
.hom-sep { width: 1px; height: 18px; background: var(--hom-line); margin: 0 4px; }

/* Top bar ---------------------------------------------------------------------------------- */
.hom-top {
  position: absolute; top: 14px; left: 16px; right: 16px; z-index: 3;
  display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; pointer-events: none;
}
.hom-top > * { pointer-events: auto; }
.hom-brand { display: flex; flex-direction: column; gap: 7px; padding: 2px 2px; }
.hom-brand-row { display: flex; align-items: center; gap: 10px; }
.hom-title {
  font: 600 13px/1 var(--font-mono, ui-monospace, monospace); letter-spacing: .32em; text-transform: uppercase;
  color: var(--ui-text-primary);
}
.hom-subtitle { color: var(--ui-text-tertiary); font-size: 11.5px; }
.hom-status {
  display: inline-flex; align-items: center; gap: 7px; height: 22px; padding: 0 9px 0 8px;
  border-radius: 999px; border: 1px solid var(--hom-line); background: var(--hom-glass);
  font: 600 9.5px/1 var(--font-mono, ui-monospace, monospace); letter-spacing: .14em; text-transform: uppercase;
  color: var(--ui-text-secondary); white-space: nowrap;
}
.hom-status b { font-weight: 600; color: var(--ui-text-primary); }
.hom-status-rate { color: var(--ui-text-tertiary); letter-spacing: .06em; }
.hom-dot { position: relative; width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }
.hom-dot[data-pulse="true"]::after {
  content: ""; position: absolute; inset: -4px; border-radius: 50%; border: 1px solid currentColor;
  animation: hom-ping 1.8s var(--hom-ease) infinite;
}
@keyframes hom-ping { 0% { transform: scale(.4); opacity: .9; } 80%, 100% { transform: scale(1.5); opacity: 0; } }
.hom-tone-live { color: var(--hom-live); }
.hom-tone-warn { color: var(--hom-warn); }
.hom-tone-bad { color: var(--hom-bad); }
.hom-tone-demo { color: var(--hom-demo); }
.hom-tone-idle { color: var(--ui-text-tertiary); }

.hom-global { padding: 13px 14px 12px; display: flex; flex-direction: column; gap: 6px; }
.hom-global-value { font: 300 30px/1 var(--font-sans, inherit); letter-spacing: -.01em; }
.hom-global-foot { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }

/* Rails ------------------------------------------------------------------------------------ */
.hom-rail {
  position: absolute; top: 96px; bottom: 82px; z-index: 2;
  display: flex; flex-direction: column; gap: 10px;
  overflow-y: auto; overflow-x: hidden; scrollbar-width: none; overscroll-behavior: contain;
  pointer-events: none; padding: 1px;
}
.hom-rail::-webkit-scrollbar { display: none; }
.hom-rail > * { pointer-events: auto; flex: none; }
.hom-rail--left { left: 16px; width: 276px; }
.hom-rail--right { right: 16px; width: 324px; }
.hom-card { padding: 13px 14px; display: flex; flex-direction: column; gap: 10px; }
.hom-card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 16px; }
.hom-big { font: 300 30px/1 var(--font-sans, inherit); letter-spacing: -.01em; }
.hom-big-row { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.hom-kv { display: flex; gap: 12px; flex-wrap: wrap; color: var(--ui-text-tertiary); font-size: 11px; }
.hom-kv b { color: var(--ui-text-secondary); font-weight: 500; }

.hom-chips { display: flex; flex-direction: column; gap: 2px; }
.hom-chip {
  appearance: none; border: 0; background: transparent; font: inherit; color: inherit; text-align: left;
  position: relative; display: grid; grid-template-columns: 16px 1fr auto 38px; align-items: center; gap: 8px;
  padding: 6px 7px; margin: 0 -7px; border-radius: 7px; cursor: pointer;
  transition: background .18s var(--hom-ease), opacity .18s var(--hom-ease);
}
.hom-chip:hover { background: var(--ui-row-hover-background, color-mix(in oklab, var(--ui-text-primary) 6%, transparent)); }
.hom-chip[aria-pressed="false"] { opacity: .42; }
.hom-chip[aria-pressed="false"] .hom-chip-name { text-decoration: line-through; text-decoration-color: var(--ui-text-tertiary); }
.hom-chip-name { font-weight: 500; }
.hom-chip-count { color: var(--ui-text-secondary); }
.hom-chip-share { color: var(--ui-text-tertiary); text-align: right; font-size: 10.5px; }
.hom-chip-bar { position: absolute; left: 31px; right: 7px; bottom: 2px; height: 2px; border-radius: 2px; background: var(--hom-line-soft); overflow: hidden; }
.hom-chip-bar > i { display: block; height: 100%; border-radius: inherit; transition: width .6s var(--hom-ease); }

.hom-toggle { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--ui-text-secondary); font-size: 11.5px; }

.hom-tabs { display: flex; gap: 2px; padding: 2px; border-radius: 8px; background: color-mix(in oklab, var(--ui-text-primary) 5%, transparent); }
.hom-tab {
  appearance: none; border: 0; background: transparent; font: inherit; color: var(--ui-text-tertiary); cursor: pointer;
  flex: 1; height: 22px; border-radius: 6px; font-size: 11px; font-weight: 500;
  transition: background .18s var(--hom-ease), color .18s var(--hom-ease);
}
.hom-tab[aria-selected="true"] { background: var(--hom-glass-strong); color: var(--ui-text-primary); box-shadow: 0 0 0 1px var(--hom-line); }
.hom-rank { display: flex; flex-direction: column; gap: 1px; }
.hom-row {
  appearance: none; border: 0; background: transparent; font: inherit; color: inherit; text-align: left; width: 100%;
  position: relative; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px;
  padding: 5px 7px; margin: 0 -7px; width: calc(100% + 14px); border-radius: 7px; cursor: pointer;
}
.hom-row:hover { background: var(--ui-row-hover-background, color-mix(in oklab, var(--ui-text-primary) 6%, transparent)); }
.hom-row-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hom-row-bar { position: absolute; left: 7px; right: 7px; bottom: 1px; height: 2px; }
.hom-row-bar > i { display: block; height: 100%; border-radius: 2px; background: color-mix(in oklab, var(--ui-accent) 55%, transparent); transition: width .6s var(--hom-ease); }
.hom-stack { display: flex; height: 100%; width: 100%; border-radius: 2px; overflow: hidden; background: var(--hom-line-soft); }
.hom-stack > i { display: block; height: 100%; transition: width .6s var(--hom-ease); }
.hom-empty-note { color: var(--ui-text-tertiary); font-size: 11.5px; line-height: 1.5; padding: 4px 0; }
.hom-caption { color: var(--ui-text-tertiary); font-size: 10.5px; line-height: 1.45; }

/* Feed ------------------------------------------------------------------------------------- */
.hom-feed { display: flex; flex-direction: column; min-height: 0; flex: 1 1 auto !important; padding-bottom: 6px; }
.hom-feed-head { padding: 13px 14px 8px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.hom-feed-list { overflow-y: auto; overflow-x: hidden; flex: 1; min-height: 0; padding: 0 6px; scrollbar-width: thin; scrollbar-color: var(--hom-line) transparent; }
.hom-event {
  appearance: none; border: 0; background: transparent; font: inherit; color: inherit; text-align: left; width: 100%;
  display: grid; grid-template-columns: 14px 1fr auto; gap: 9px; align-items: start;
  padding: 7px 8px; border-radius: 8px; cursor: pointer; position: relative;
  transition: background .18s var(--hom-ease);
}
.hom-event:hover, .hom-event[data-active="true"] { background: var(--ui-row-hover-background, color-mix(in oklab, var(--ui-text-primary) 6%, transparent)); }
.hom-event[data-fresh="true"] { animation: hom-row-in .55s var(--hom-ease) both; }
@keyframes hom-row-in {
  0% { opacity: 0; transform: translateY(-8px); background: color-mix(in oklab, var(--hom-row-color) 22%, transparent); }
  45% { opacity: 1; transform: none; background: color-mix(in oklab, var(--hom-row-color) 14%, transparent); }
  100% { background: transparent; }
}
.hom-event-glyph { padding-top: 2px; }
.hom-event-name { font-size: 12px; line-height: 1.3; font-weight: 500; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.hom-event-route { display: flex; align-items: center; gap: 5px; margin-top: 4px; color: var(--ui-text-tertiary); font-size: 10.5px; min-width: 0; }
.hom-event-route span.hom-ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.hom-event-side { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; padding-top: 1px; }
.hom-event-age { font: 500 10px/1 var(--font-mono, ui-monospace, monospace); color: var(--ui-text-tertiary); }
.hom-badge {
  font: 600 9.5px/1 var(--font-mono, ui-monospace, monospace); padding: 2px 5px; border-radius: 4px;
  color: var(--ui-text-secondary); background: color-mix(in oklab, var(--ui-text-primary) 7%, transparent);
}
.hom-badge--demo { color: var(--hom-demo); background: color-mix(in oklab, var(--hom-demo) 14%, transparent); letter-spacing: .08em; }
.hom-paused-note {
  margin: 0 8px 6px; padding: 6px 9px; border-radius: 7px; font-size: 11px; color: var(--ui-text-secondary);
  background: color-mix(in oklab, var(--hom-warn) 12%, transparent); display: flex; align-items: center; gap: 6px;
}

/* Detail panels ---------------------------------------------------------------------------- */
.hom-detail { animation: hom-rise .38s var(--hom-ease) both; }
.hom-detail-title { font-size: 17px; line-height: 1.25; font-weight: 600; letter-spacing: -.005em; }
.hom-detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.hom-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.hom-pair > div { display: flex; flex-direction: column; gap: 5px; padding: 9px 10px; border-radius: 9px; background: color-mix(in oklab, var(--ui-text-primary) 4%, transparent); }
.hom-pair-value { font: 300 22px/1 var(--font-sans, inherit); }
.hom-route { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: center; }
.hom-route-end { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.hom-route-end strong { font-weight: 500; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hom-route-arrow { color: var(--ui-text-tertiary); }
.hom-facts { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; font-size: 11.5px; }
.hom-facts dt { color: var(--ui-text-tertiary); }
.hom-facts dd { margin: 0; color: var(--ui-text-secondary); }
.hom-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.hom-chart { display: block; width: 100%; overflow: visible; }
.hom-chart text { font: 500 9px var(--font-mono, ui-monospace, monospace); fill: var(--ui-text-tertiary); }
.hom-multiples { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.hom-multiple { display: flex; flex-direction: column; gap: 3px; }
.hom-multiple-head { display: flex; justify-content: space-between; font-size: 10.5px; color: var(--ui-text-secondary); }
.hom-skeleton { height: 64px; border-radius: 8px; background: linear-gradient(90deg, var(--hom-line-soft), color-mix(in oklab, var(--ui-text-primary) 8%, transparent), var(--hom-line-soft)); background-size: 200% 100%; animation: hom-shimmer 1.4s linear infinite; }
@keyframes hom-shimmer { to { background-position: -200% 0; } }

/* Dock ------------------------------------------------------------------------------------- */
.hom-dock {
  position: absolute; left: 50%; bottom: 16px; transform: translateX(-50%); z-index: 3;
  display: flex; align-items: center; gap: 2px; padding: 4px; border-radius: 12px;
}
.hom-seg { display: inline-flex; padding: 2px; border-radius: 8px; background: color-mix(in oklab, var(--ui-text-primary) 5%, transparent); }
.hom-seg .hom-btn { height: 26px; font-size: 11px; font-weight: 500; padding: 0 9px; }
.hom-seg .hom-btn[aria-pressed="true"] { background: var(--hom-glass-strong); box-shadow: 0 0 0 1px var(--hom-line); }

.hom-attrib {
  position: absolute; left: 18px; bottom: 20px; z-index: 2; max-width: 30%;
  color: var(--ui-text-quaternary, var(--ui-text-tertiary)); font-size: 10px; line-height: 1.4;
}
.hom-attrib a { color: inherit; }

/* Overlays --------------------------------------------------------------------------------- */
.hom-center {
  position: absolute; inset: 0; z-index: 4; display: grid; place-items: center; pointer-events: none; padding: 24px;
}
.hom-center > * { pointer-events: auto; }
.hom-dialog { width: min(440px, 100%); padding: 22px 22px 18px; display: flex; flex-direction: column; gap: 14px; animation: hom-rise .5s var(--hom-ease) both; background: var(--hom-glass-strong); }
.hom-dialog h2 { margin: 0; font-size: 19px; font-weight: 600; letter-spacing: -.01em; }
.hom-dialog p { margin: 0; color: var(--ui-text-secondary); line-height: 1.55; font-size: 12.5px; }
.hom-facts-list { display: flex; flex-direction: column; gap: 9px; margin: 2px 0; padding: 0; list-style: none; }
.hom-facts-list li { display: grid; grid-template-columns: 16px 1fr; gap: 9px; color: var(--ui-text-secondary); font-size: 12px; line-height: 1.5; }
.hom-facts-list .codicon { color: var(--ui-text-tertiary); margin-top: 2px; }
.hom-dialog-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
.hom-banner {
  position: absolute; left: 50%; transform: translateX(-50%); z-index: 4;
  display: inline-flex; align-items: center; gap: 9px; padding: 7px 8px 7px 12px; border-radius: 999px;
  font-size: 11.5px; color: var(--ui-text-secondary); white-space: nowrap; animation: hom-rise .4s var(--hom-ease) both;
}
.hom-banner--top { top: 62px; }
.hom-banner--bottom { bottom: 66px; padding: 6px 14px; }
.hom-demo-strip {
  font: 600 9.5px/1 var(--font-mono, ui-monospace, monospace); letter-spacing: .16em; text-transform: uppercase;
  color: var(--hom-demo);
  background:
    repeating-linear-gradient(-45deg, color-mix(in oklab, var(--hom-demo) 13%, transparent) 0 6px, transparent 6px 12px),
    var(--hom-glass);
  border: 1px solid color-mix(in oklab, var(--hom-demo) 40%, transparent);
}
.hom-listening { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); z-index: 2; pointer-events: none;
  display: flex; align-items: center; gap: 10px; padding: 9px 14px; border-radius: 999px; color: var(--ui-text-secondary); font-size: 12px; }

.hom-tip {
  position: absolute; left: 0; top: 0; z-index: 5; pointer-events: none; padding: 9px 11px; min-width: 150px; max-width: 280px;
  border-radius: 10px; display: flex; flex-direction: column; gap: 4px; font-size: 11.5px;
  background: var(--hom-glass-strong); transition: opacity .12s;
}
.hom-tip-title { font-weight: 600; font-size: 12.5px; display: flex; align-items: center; gap: 7px; }
.hom-callout { position: absolute; left: 0; top: 0; z-index: 1; pointer-events: none; display: flex; align-items: center; gap: 0; transition: opacity .2s; }
.hom-callout-dot { width: 9px; height: 9px; border-radius: 50%; border: 2px solid var(--ui-accent); background: var(--ui-bg-editor, var(--ui-base)); transform: translate(-50%, -50%); flex: none; }
.hom-callout-line { width: 26px; height: 1px; background: var(--ui-accent); transform: translateY(-50%); margin-left: -4px; }
.hom-callout-tag {
  transform: translateY(-50%); padding: 4px 8px; border-radius: 6px; font: 600 10px/1 var(--font-mono, ui-monospace, monospace);
  letter-spacing: .12em; text-transform: uppercase; color: var(--ui-text-primary); background: var(--hom-glass-strong); border: 1px solid color-mix(in oklab, var(--ui-accent) 55%, transparent);
}

/* OSINT additions ----------------------------------------------------------------------------- */
.hom-top-actions { display: flex; gap: 8px; align-items: center; }
.hom-clock { font-size: 11px; color: var(--ui-text-secondary); letter-spacing: .04em; padding: 0 2px; }
.hom-layer { grid-template-columns: 16px 1fr auto 44px; }
.hom-layer-state { display: inline-flex; align-items: center; justify-content: flex-end; gap: 5px; font-size: 10px; color: var(--ui-text-tertiary); }
.hom-layer-state .hom-dot { width: 6px; height: 6px; }
.hom-scales { display: inline-flex; gap: 4px; }
.hom-scale {
  font: 600 9.5px/1 var(--font-mono, ui-monospace, monospace); padding: 3px 5px; border-radius: 4px;
  color: var(--ui-text-secondary); background: color-mix(in oklab, var(--ui-text-primary) 6%, transparent);
}
.hom-scale[data-level="1"], .hom-scale[data-level="2"] { color: var(--ui-yellow, var(--ui-orange)); background: color-mix(in oklab, var(--ui-yellow, var(--ui-orange)) 14%, transparent); }
.hom-scale[data-level="3"], .hom-scale[data-level="4"], .hom-scale[data-level="5"] { color: var(--ui-red); background: color-mix(in oklab, var(--ui-red) 16%, transparent); }
.hom-change { font-size: 10.5px; min-width: 58px; text-align: right; }
.hom-change[data-up="true"] { color: var(--ui-green, var(--ui-text-secondary)); }
.hom-change[data-up="false"] { color: var(--ui-red, var(--ui-text-secondary)); }
.hom-feed-filter { display: flex; flex-wrap: wrap; gap: 4px; padding: 0 14px 8px; }
.hom-tag--button[aria-pressed="true"] { color: var(--ui-text-primary); border-color: color-mix(in oklab, var(--ui-accent) 55%, transparent); background: color-mix(in oklab, var(--ui-accent) 12%, transparent); }
.hom-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.hom-tag {
  display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px; border-radius: 6px; font-size: 10.5px;
  color: var(--ui-text-secondary); background: color-mix(in oklab, var(--ui-text-primary) 5%, transparent); border: 1px solid var(--hom-line-soft);
}
.hom-tag--button { appearance: none; font: inherit; font-size: 10.5px; cursor: pointer; }
.hom-tag--button:hover { color: var(--ui-text-primary); border-color: var(--hom-line); }
.hom-recon { flex: 1 1 auto !important; overflow: auto; }
.hom-recon-kinds { display: flex; flex-wrap: wrap; gap: 2px; padding: 2px; border-radius: 8px; background: color-mix(in oklab, var(--ui-text-primary) 5%, transparent); }
.hom-recon-kinds .hom-tab { flex: 0 0 auto; padding: 0 8px; }
.hom-recon-form { display: flex; gap: 6px; }
.hom-input {
  flex: 1; min-width: 0; height: 34px; padding: 0 10px; border-radius: 8px; font-size: 12px;
  color: var(--ui-text-primary); background: color-mix(in oklab, var(--ui-text-primary) 5%, transparent);
  border: 1px solid var(--hom-line); outline: none;
}
.hom-input:focus { border-color: color-mix(in oklab, var(--ui-accent) 70%, transparent); }
.hom-btn:disabled { opacity: .45; cursor: default; }
.hom-recon-result { display: flex; flex-direction: column; gap: 10px; padding-top: 6px; border-top: 1px solid var(--hom-line-soft); }
.hom-recon-result .hom-facts dd { word-break: break-word; }
.hom-raw {
  margin: 0; max-height: 240px; overflow: auto; padding: 10px; border-radius: 8px; font: 10.5px/1.45 var(--font-mono, ui-monospace, monospace);
  color: var(--ui-text-secondary); background: color-mix(in oklab, var(--ui-text-primary) 4%, transparent); user-select: text;
}
.hom-error { padding: 8px 10px; border-radius: 8px; font-size: 11.5px; color: var(--ui-text-primary); background: color-mix(in oklab, var(--ui-red) 14%, transparent); }
.hom-listening-inline { display: flex; align-items: center; gap: 8px; color: var(--ui-text-secondary); font-size: 11.5px; }
.hom-facts dd { user-select: text; }

@keyframes hom-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.hom-rise { animation: hom-rise .6s var(--hom-ease) both; }
.hom-banner.hom-rise, .hom-banner { animation-name: hom-rise-x; }
@keyframes hom-rise-x { from { opacity: 0; transform: translate(-50%, 8px); } to { opacity: 1; transform: translate(-50%, 0); } }

/* Rolling digits */
.hom-roll { display: inline-flex; align-items: baseline; }
.hom-roll-digit { display: inline-block; height: 1em; line-height: 1em; overflow: hidden; position: relative; }
.hom-roll-strip { display: flex; flex-direction: column; transition: transform .9s var(--hom-ease); }
.hom-roll-strip > span { height: 1em; line-height: 1em; }

/* Responsive: the page also runs in split tiles. */
.hom-root[data-size="compact"] .hom-rail--left { width: 244px; }
.hom-root[data-size="compact"] .hom-rail--right { width: 284px; }
.hom-root[data-size="compact"] .hom-attrib { display: none; }
.hom-root[data-size="narrow"] .hom-rail { top: 84px; bottom: 74px; }
.hom-root[data-size="narrow"] .hom-rail--left, .hom-root[data-size="narrow"] .hom-rail--right { left: 12px; right: 12px; width: auto; }
.hom-root[data-size="narrow"] .hom-rail[data-open="false"] { display: none; }
.hom-root[data-size="narrow"] .hom-attrib { display: none; }
.hom-root[data-size="narrow"] .hom-subtitle { display: none; }
.hom-root[data-size="narrow"] .hom-top { left: 12px; right: 12px; }

.hom-root[data-motion="reduced"] *, .hom-root[data-motion="reduced"] *::before, .hom-root[data-motion="reduced"] *::after {
  animation-duration: 1ms !important; animation-iteration-count: 1 !important; transition-duration: 1ms !important;
}
@media (prefers-reduced-motion: reduce) {
  .hom-root *, .hom-root *::before, .hom-root *::after { animation-duration: 1ms !important; animation-iteration-count: 1 !important; transition-duration: 1ms !important; }
}
`
