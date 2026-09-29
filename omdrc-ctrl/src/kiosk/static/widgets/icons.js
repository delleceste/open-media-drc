/* Transport icons: thin line drawings instead of the ⏮ ⏸ ⏹ ⏭ ▶ characters, which
 * Android draws as coloured emoji tiles (orange squares for pause and stop).
 *   K.tIcon('play' | 'pause' | 'stop' | 'prev' | 'next') -> <svg class="ticon">
 * The stroke is the text colour (currentColor). */
(() => {
const NS = 'http://www.w3.org/2000/svg';
const SHAPES = {
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
