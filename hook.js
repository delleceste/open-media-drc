(() => {
  window.__log = [];
  const L = (...a) => __log.push([Math.round(performance.now()), ...a]);
  const P = K.pages.find(p => p.id === 'qobuz');
  const o = K.setPref; K.setPref = (k, v) => { if (k === 'qobuz.view') L('VIEW', v, new Error().stack); return o(k, v); };
  const sp = K.showPage; K.showPage = (...a) => { L('showPage', a, new Error().stack); return sp(...a); };
  for (const f of ['setView', 'show', 'hide', 'prepareEnter', 'undock', 'openResults', 'search']) {
    const g = P[f]; P[f] = (...a) => { L(f, JSON.stringify(a).slice(0, 80), new Error().stack.split('\n').slice(2, 6).join(' | ')); return g(...a); };
  }
  for (const ev of ['touchstart', 'touchend', 'pointercancel'])
    document.addEventListener(ev, e => L(ev, e.target.className || e.target.tagName, P.el && P.el.scrollTop), { capture: true, passive: true });
  addEventListener('resize', () => L('resize', innerWidth, innerHeight));
  return 'hooked, view=' + P.view();
})()
