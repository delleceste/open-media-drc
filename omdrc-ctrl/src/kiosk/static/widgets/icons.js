/* Transport icons: thin line drawings instead of the ⏮ ⏸ ⏹ ⏭ ▶ characters, which
 * Android draws as coloured emoji tiles (orange squares for pause and stop).
 *   K.tIcon('play' | 'pause' | 'stop' | 'prev' | 'next') -> <svg class="ticon">
 * The stroke is the text colour (currentColor). */
(() => {
const NS = 'http://www.w3.org/2000/svg';
const SHAPES = {
    filter_down: [['path', { d: 'M3 4 H21 L14 12 V18 L10 21 V12 Z M17 17 L20 20 L23 17' }]],
    filter_up: [['path', { d: 'M3 4 H21 L14 12 V18 L10 21 V12 Z M17 20 L20 17 L23 20' }]],
    close: [['path', { d: 'M6 6 L18 18 M18 6 L6 18' }]],
    clear: [['path', { d: 'M20 3 L12 11 M10 9 L15 14 L12 21 L3 12 Z M7 11 L13 17 M6 15 L9 12 M9 18 L12 15' }]],
    search: [['circle', { cx: 10, cy: 10, r: 6 }], ['path', { d: 'M14.5 14.5 L21 21' }]],
    play: [['path', { d: 'M8 5.5 L18.5 12 L8 18.5 Z' }]],
    pause: [['path', { d: 'M9 6 V18 M15 6 V18' }]],
    stop: [['rect', { x: 6.5, y: 6.5, width: 11, height: 11, rx: 1.8 }]],
    prev: [['path', { d: 'M6.5 6 V18' }], ['path', { d: 'M18 6.5 L9.5 12 L18 17.5 Z' }]],
    next: [['path', { d: 'M17.5 6 V18' }], ['path', { d: 'M6 6.5 L14.5 12 L6 17.5 Z' }]],
};
K.tIcon = name => {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', 'ticon');
    for (const [tag, attrs] of SHAPES[name] || SHAPES.play) {
        const el = document.createElementNS(NS, tag);
        Object.entries({ fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8,
            'stroke-linecap': 'round', 'stroke-linejoin': 'round', ...attrs }).forEach(([k, v]) => el.setAttribute(k, v));
        svg.append(el);
    }
    return svg;
};
})();
