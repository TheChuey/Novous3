/**
 * Draggable side panels shared by the Code Builder and the Chat Console.
 *
 * Each panel's width lives in a CSS variable on the layout. Dragging its divider resizes it;
 * releasing narrower than `hideBelow` hides the panel completely (`<name>-collapsed` on the
 * shell), and the divider becomes a slim tab that brings it back on click or drag. Panels can
 * widen until only `centerMin` pixels remain for the middle pane. Widths and hidden state are
 * remembered per `prefsKey`.
 */

const COLLAPSED_GUTTER = 18;

/**
 * @param {{
 *   shell: HTMLElement, layout: HTMLElement, prefsKey: string, centerMin?: number,
 *   panels: Record<string, {var: string, edge: 'left'|'right', min: number, hideBelow: number,
 *     default: number, label: string, resizer: HTMLElement, toggle?: HTMLElement|null}>
 * }} options
 */
export function setupSidePanels({ shell, layout, prefsKey, centerMin = 280, panels }) {
  const readPref = key => {
    try { return localStorage.getItem(prefsKey + key); } catch { return null; }
  };
  const writePref = (key, value) => {
    try { localStorage.setItem(prefsKey + key, value); } catch { /* storage unavailable */ }
  };
  const readVar = name => parseFloat(
    layout.style.getPropertyValue(name) || getComputedStyle(layout).getPropertyValue(name)
  );
  const isCollapsed = name => shell.classList.contains(name + '-collapsed');

  // Widest a panel may get: the layout minus the middle pane's minimum and the other panels.
  const maxSize = name => {
    const others = Object.keys(panels)
      .filter(other => other !== name && !isCollapsed(other))
      .reduce((sum, other) => sum + (readVar(panels[other].var) || 0), 0);
    const gutters = Object.keys(panels).length * COLLAPSED_GUTTER;
    return Math.max(panels[name].min, layout.clientWidth - others - centerMin - gutters);
  };
  const clampSize = (name, value) =>
    Math.round(Math.min(Math.max(value, panels[name].min), maxSize(name)));

  const refreshToggle = name => {
    const { toggle, resizer, label } = panels[name];
    const collapsed = isCollapsed(name);
    const text = `${collapsed ? 'Show' : 'Hide'} ${label} panel`;
    if (toggle) {
      toggle.title = text;
      toggle.setAttribute('aria-label', text);
      toggle.setAttribute('aria-expanded', String(!collapsed));
    }
    resizer.title = collapsed
      ? `Click or drag to show the ${label} panel`
      : 'Drag to resize · drag to the edge or double-click to hide';
    resizer.setAttribute('aria-expanded', String(!collapsed));
  };

  const setCollapsed = (name, collapsed) => {
    const conf = panels[name];
    if (collapsed) {
      const current = readVar(conf.var);
      if (!isCollapsed(name) && Number.isFinite(current) && current >= conf.min) {
        writePref(name + 'Width', String(current));
      }
      shell.classList.add(name + '-collapsed');
      layout.style.setProperty(conf.var, '0px');
    } else {
      const saved = parseFloat(readPref(name + 'Width'));
      shell.classList.remove(name + '-collapsed');
      layout.style.setProperty(conf.var, clampSize(name, Number.isFinite(saved) ? saved : conf.default) + 'px');
    }
    writePref(name + 'Collapsed', collapsed ? '1' : '0');
    refreshToggle(name);
    window.dispatchEvent(new Event('resize'));
  };

  for (const [name, conf] of Object.entries(panels)) {
    const saved = parseFloat(readPref(name + 'Width'));
    if (Number.isFinite(saved)) layout.style.setProperty(conf.var, Math.max(saved, conf.min) + 'px');
    if (readPref(name + 'Collapsed') === '1') {
      shell.classList.add(name + '-collapsed');
      layout.style.setProperty(conf.var, '0px');
    }

    const { resizer, toggle } = conf;
    // Dragging toward the panel's own edge shrinks it.
    const direction = conf.edge === 'left' ? 1 : -1;

    resizer.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startX = event.clientX;
      const wasCollapsed = isCollapsed(name);
      const start = wasCollapsed ? 0 : (readVar(conf.var) || conf.default);
      let moved = false;
      resizer.setPointerCapture(event.pointerId);
      document.body.classList.add('is-resizing');
      resizer.classList.add('is-dragging');

      const onMove = moveEvent => {
        const delta = (moveEvent.clientX - startX) * direction;
        if (!moved && Math.abs(delta) < 4) return;
        moved = true;
        const raw = start + delta;
        if (isCollapsed(name)) shell.classList.remove(name + '-collapsed');
        // Below the minimum the panel follows the pointer down to nothing, then hides on release.
        const width = raw < conf.min ? Math.max(0, Math.round(raw)) : clampSize(name, raw);
        shell.classList.toggle(name + '-hiding', raw < conf.hideBelow);
        layout.style.setProperty(conf.var, width + 'px');
      };
      const onUp = () => {
        resizer.removeEventListener('pointermove', onMove);
        resizer.removeEventListener('pointerup', onUp);
        resizer.removeEventListener('pointercancel', onUp);
        document.body.classList.remove('is-resizing');
        resizer.classList.remove('is-dragging');
        shell.classList.remove(name + '-hiding');
        if (!moved) {
          // A click on the slim tab of a hidden panel brings it back.
          if (wasCollapsed) setCollapsed(name, false);
          return;
        }
        const width = readVar(conf.var);
        if (!Number.isFinite(width) || width < conf.hideBelow) {
          setCollapsed(name, true);
          return;
        }
        const final = clampSize(name, width);
        layout.style.setProperty(conf.var, final + 'px');
        writePref(name + 'Width', String(final));
        writePref(name + 'Collapsed', '0');
        refreshToggle(name);
      };
      resizer.addEventListener('pointermove', onMove);
      resizer.addEventListener('pointerup', onUp);
      resizer.addEventListener('pointercancel', onUp);
    });

    resizer.addEventListener('dblclick', () => setCollapsed(name, !isCollapsed(name)));
    resizer.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        setCollapsed(name, !isCollapsed(name));
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        if (isCollapsed(name)) { setCollapsed(name, false); return; }
        const step = (event.key === 'ArrowRight' ? 1 : -1) * direction * (event.shiftKey ? 80 : 24);
        const next = clampSize(name, (readVar(conf.var) || conf.default) + step);
        layout.style.setProperty(conf.var, next + 'px');
        writePref(name + 'Width', String(next));
      }
    });
    toggle?.addEventListener('click', () => setCollapsed(name, !isCollapsed(name)));
    refreshToggle(name);
  }

  // Keep panels inside the window when it shrinks.
  const onWindowResize = () => {
    if (!layout.isConnected) { window.removeEventListener('resize', onWindowResize); return; }
    for (const [name, conf] of Object.entries(panels)) {
      if (isCollapsed(name)) continue;
      const current = readVar(conf.var);
      if (Number.isFinite(current) && current > maxSize(name)) {
        layout.style.setProperty(conf.var, clampSize(name, current) + 'px');
      }
    }
  };
  window.addEventListener('resize', onWindowResize);

  return { isCollapsed, setCollapsed };
}
