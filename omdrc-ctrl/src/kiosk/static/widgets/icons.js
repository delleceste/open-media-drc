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
    clock: [['circle', { cx: 12, cy: 12, r: 8.5 }], ['path', { d: 'M12 7 V12 L15.5 14' }]],
    // Qobuz's Discover tab: a compass, its needle a tilted diamond
    compass: [['circle', { cx: 12, cy: 12, r: 8.5 }], ['path', { d: 'M15.5 8.5 L13.2 13.2 L8.5 15.5 L10.8 10.8 Z' }]],
    // the award cup (the 🏆 on awarded albums), in line
    trophy: [['path', { d: 'M8 4.5 H16 V10 A4 4 0 0 1 8 10 Z' }], ['path', { d: 'M8 6 H5 V7.5 A3 3 0 0 0 8.4 10.5 M16 6 H19 V7.5 A3 3 0 0 1 15.6 10.5' }],
        ['path', { d: 'M12 14 V17 M8.5 19.5 H15.5 M9.5 19.5 L10 17 H14 L14.5 19.5' }]],
    grid: [['rect', { x: 4, y: 4, width: 6.5, height: 6.5, rx: 1 }], ['rect', { x: 13.5, y: 4, width: 6.5, height: 6.5, rx: 1 }],
        ['rect', { x: 4, y: 13.5, width: 6.5, height: 6.5, rx: 1 }], ['rect', { x: 13.5, y: 13.5, width: 6.5, height: 6.5, rx: 1 }]],
    list: [['rect', { x: 3.5, y: 5, width: 4.5, height: 4.5, rx: .8 }], ['rect', { x: 3.5, y: 14.5, width: 4.5, height: 4.5, rx: .8 }],
        ['path', { d: 'M11 6.5 H20.5 M11 9 H17 M11 16 H20.5 M11 18.5 H17' }]],
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
