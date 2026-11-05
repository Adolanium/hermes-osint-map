import { LAYERS } from '../data/layers.js'

const LAYER_VARS = LAYERS.map((l, i) => `  --hom-l${i}: var(${l.cssVar}, var(${l.fallbackVar}));`).join('\n')

// Card styles. Every colour is a theme variable, so the card reskins with the chat around it.
export const CARD_CSS = /* css */ `
.homc {
${LAYER_VARS}
  --homc-line: color-mix(in oklab, var(--ui-stroke-secondary) 70%, transparent);
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  max-width: 620px;
  margin: 6px 0;
  padding: 12px 14px 12px 12px;
  border: 1px solid var(--homc-line);
  border-radius: 14px;
  background: color-mix(in oklab, var(--ui-bg-editor, var(--ui-base)) 88%, var(--ui-text-primary) 3%);
  color: var(--ui-text-primary);
  font-size: 12px;
  line-height: 1.35;
}
.homc *, .homc *::before, .homc *::after { box-sizing: border-box; }
.homc-globe { position: relative; width: 168px; height: 168px; }
.homc-canvas { display: block; width: 168px; height: 168px; border-radius: 50%; cursor: pointer; }
.homc[data-zoomed="true"] .homc-canvas { border-radius: 10px; }
.homc-globe-wait { position: absolute; inset: 0; display: grid; place-items: center; }
.homc-body { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.homc-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.homc-kicker {
  display: inline-flex; align-items: center; gap: 6px;
  font: 500 9.5px/1.2 var(--font-mono, ui-monospace, monospace); letter-spacing: .14em; text-transform: uppercase;
  color: var(--ui-text-tertiary);
}
.homc-live { width: 6px; height: 6px; border-radius: 50%; background: var(--ui-green, var(--ui-accent)); }
.homc-live[data-ok="false"] { background: var(--ui-yellow, var(--ui-orange)); }
.homc-muted { color: var(--ui-text-tertiary); font-size: 11px; }
.homc-num { font-variant-numeric: tabular-nums; }
.homc-title { font-size: 15px; font-weight: 600; letter-spacing: -.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.homc-sub { color: var(--ui-text-secondary); font-size: 11.5px; }
.homc-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.homc-chip {
  display: inline-flex; align-items: center; gap: 5px; padding: 2px 7px 2px 6px;
  border: 1px solid var(--homc-line); border-radius: 999px; font-size: 11px; color: var(--ui-text-secondary);
}
.homc-chip-label { color: var(--ui-text-tertiary); }
.homc-list { list-style: none; margin: 2px 0 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
.homc-row {
  all: unset; box-sizing: border-box; width: 100%; display: grid; grid-template-columns: 12px minmax(0, 1fr) auto;
  align-items: center; gap: 8px; padding: 4px 6px; border-radius: 7px; cursor: pointer;
}
.homc-row:hover { background: color-mix(in oklab, var(--ui-text-primary) 6%, transparent); }
.homc-row[data-picked="true"] { background: color-mix(in oklab, var(--ui-accent) 16%, transparent); }
.homc-row:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 1px; }
.homc-row-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.homc-row-meta { color: var(--ui-text-tertiary); font-size: 11px; white-space: nowrap; }
.homc-quiet, .homc-error { color: var(--ui-text-tertiary); font-size: 11.5px; padding: 4px 0; }
.homc-error { color: var(--ui-yellow, var(--ui-orange)); }
.homc-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
@media (max-width: 520px) {
  .homc { grid-template-columns: 1fr; }
  .homc-globe { margin: 0 auto; }
}
`
