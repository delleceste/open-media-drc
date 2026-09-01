/* Axis navigation for the verified response charts.
 *
 * Chart.js ships without a zoom plugin and this page is served from the
 * appliance itself, so the interaction lives here: log-domain panning and
 * zooming on the frequency axis, linear panning and zooming on the level
 * axis, pointer gestures for the phone and explicit buttons for everything
 * a gesture cannot say out loud.
 *
 * Nothing in here touches the plotted numbers.  It only decides which part
 * of them a chart is currently showing.
 */
(function (root) {
    'use strict';

    // Hard stops, so a fling or a fast pinch cannot leave the data behind.
    const FREQ_FLOOR_HZ = 5;
    const FREQ_CEILING_HZ = 24000;
    const MIN_FREQ_RATIO = 1.15;      // narrowest frequency window, ~2 semitones
    const MIN_SPAN_DB = 0.5;          // narrowest level window
    const MAX_SPAN_DB = 400;

    // The bass is what this appliance corrects, so that is where the view
    // starts.  The rest is one button away.
    const DEFAULT_FREQUENCY_RANGE = {min: 15, max: 450};

    // A 2 dB difference must read as 2 dB.  Auto-fitting to the data alone
    // turns a well-corrected room into a canyon, so the fit never shows a
    // window narrower than this.
    const MIN_AUTOFIT_SPAN_DB = 24;
    const AUTOFIT_PADDING = 0.08;

    const FREQUENCY_PRESETS = [
        {label: 'Bass', min: 15, max: 450, title: 'Default view — 15 to 450 Hz'},
        {label: 'Deep', min: 12, max: 120, title: 'Deep bass — 12 to 120 Hz'},
        {label: 'Modal', min: 20, max: 200, title: 'Room modes — 20 to 200 Hz'},
        {label: 'Mid', min: 200, max: 4000, title: 'Midrange — 200 Hz to 4 kHz'},
        {label: 'Full', min: 10, max: 20000, title: 'Everything the exports cover'},
    ];

    // 1-2-3-5 style ladders, densest first.  The axis picks the densest one
    // that still fits in the window without crowding.
    const TICK_LADDERS = [
        [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8],
        [1, 1.5, 2, 3, 5, 7],
        [1, 2, 5],
        [1],
    ];
    const MAX_TICKS = 11;

    function clamp(value, low, high) {
        return Math.min(high, Math.max(low, value));
    }

    function formatHz(value) {
        if (value >= 1000) {
            const k = value / 1000;
            return (Number.isInteger(k) ? k : k.toFixed(1).replace(/\.0$/, '')) + 'k';
        }
        return String(value >= 10 ? Math.round(value) : Math.round(value * 10) / 10);
    }

    function frequencyTicks(low, high) {
        for (const ladder of TICK_LADDERS) {
            const ticks = [];
            for (let decade = -1; decade <= 5; decade += 1) {
                const scale = Math.pow(10, decade);
                for (const step of ladder) {
                    const value = step * scale;
                    if (value >= low && value <= high) ticks.push(value);
                }
            }
            if (ticks.length <= MAX_TICKS) return ticks.length ? ticks : [low, high];
        }
        return [low, high];
    }

    /* One chart's window on the data, plus the gestures that move it. */
    class ResponseView {
        constructor(options) {
            this.getChart = options.getChart;
            this.container = options.container;
            this.onChange = options.onChange || function () {};
            this.defaultFrequency = options.defaultFrequency || DEFAULT_FREQUENCY_RANGE;
            this.frequency = Object.assign({}, this.defaultFrequency);
            this.level = null;            // null = fit to what is visible
            this.selectMode = false;
            this.pointers = new Map();
            this.gesture = null;
            this.marquee = null;
            if (this.container) this.attach(this.container);
        }

        /* ---- window state ------------------------------------------- */

        setFrequency(min, max) {
            let low = clamp(Math.min(min, max), FREQ_FLOOR_HZ, FREQ_CEILING_HZ);
            let high = clamp(Math.max(min, max), FREQ_FLOOR_HZ, FREQ_CEILING_HZ);
            if (high / low < MIN_FREQ_RATIO) {
                const centre = Math.sqrt(low * high);
                low = centre / Math.sqrt(MIN_FREQ_RATIO);
                high = centre * Math.sqrt(MIN_FREQ_RATIO);
                // Widening around the centre can push a window pinned against
                // one edge past the opposite floor/ceiling; re-clamp and slide
                // the whole window back in rather than let it escape.
                if (low < FREQ_FLOOR_HZ) { high *= FREQ_FLOOR_HZ / low; low = FREQ_FLOOR_HZ; }
                if (high > FREQ_CEILING_HZ) { low *= FREQ_CEILING_HZ / high; high = FREQ_CEILING_HZ; }
            }
            this.frequency = {min: low, max: high};
            this.onChange();
        }

        setLevel(min, max) {
            if (min === null) { this.level = null; this.onChange(); return; }
            let low = Math.min(min, max);
            let high = Math.max(min, max);
            const span = clamp(high - low, MIN_SPAN_DB, MAX_SPAN_DB);
            const centre = (low + high) / 2;
            this.level = {min: centre - span / 2, max: centre + span / 2};
            this.onChange();
        }

        /* The level window in use right now, whether pinned or fitted. */
        currentLevel(fallback) {
            if (this.level) return this.level;
            return fallback || null;
        }

        zoomFrequency(factor, anchor) {
            const {min, max} = this.frequency;
            const pivot = anchor === undefined ? Math.sqrt(min * max) : clamp(anchor, min, max);
            this.setFrequency(pivot * Math.pow(min / pivot, factor),
                              pivot * Math.pow(max / pivot, factor));
        }

        zoomLevel(factor, anchor) {
            const chart = this.getChart();
            const current = this.level || (chart && chart.scales.y
                ? {min: chart.scales.y.min, max: chart.scales.y.max} : null);
            if (!current) return;
            const pivot = anchor === undefined
                ? (current.min + current.max) / 2 : clamp(anchor, current.min, current.max);
            this.setLevel(pivot + (current.min - pivot) * factor,
                          pivot + (current.max - pivot) * factor);
        }

        panFrequency(ratio) {
            this.setFrequency(this.frequency.min * ratio, this.frequency.max * ratio);
        }

        panLevel(delta) {
            const chart = this.getChart();
            const current = this.level || (chart && chart.scales.y
                ? {min: chart.scales.y.min, max: chart.scales.y.max} : null);
            if (!current) return;
            this.setLevel(current.min + delta, current.max + delta);
        }

        reset() {
            this.frequency = Object.assign({}, this.defaultFrequency);
            this.level = null;
            this.onChange();
        }

        fitLevel() {
            this.level = null;
            this.onChange();
        }

        /* Level window that shows the visible data without flattering it:
         * never narrower than MIN_AUTOFIT_SPAN_DB, so 2 dB looks like 2 dB. */
        autoLevel(datasets, minimumSpan) {
            const floor = minimumSpan === undefined ? MIN_AUTOFIT_SPAN_DB : minimumSpan;
            let low = Infinity;
            let high = -Infinity;
            for (const dataset of datasets) {
                for (const point of dataset.data) {
                    if (point.x < this.frequency.min || point.x > this.frequency.max) continue;
                    if (!Number.isFinite(point.y)) continue;
                    if (point.y < low) low = point.y;
                    if (point.y > high) high = point.y;
                }
            }
            if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
            const padding = Math.max((high - low) * AUTOFIT_PADDING, 0.25);
            low -= padding;
            high += padding;
            if (high - low < floor) {
                const centre = (low + high) / 2;
                low = centre - floor / 2;
                high = centre + floor / 2;
            }
            return {min: low, max: high};
        }

        /* ---- gestures ------------------------------------------------ */

        attach(container) {
            const canvas = container.querySelector('canvas');
            if (!canvas) return;
            this.canvas = canvas;
            canvas.style.touchAction = 'none';
            canvas.addEventListener('pointerdown', e => this.onPointerDown(e));
            canvas.addEventListener('pointermove', e => this.onPointerMove(e));
            canvas.addEventListener('pointerup', e => this.onPointerUp(e));
            canvas.addEventListener('pointercancel', e => this.onPointerUp(e));
            canvas.addEventListener('wheel', e => this.onWheel(e), {passive: false});
            canvas.addEventListener('dblclick', e => { e.preventDefault(); this.reset(); });
        }

        plotValue(event) {
            const chart = this.getChart();
            if (!chart) return null;
            const rect = this.canvas.getBoundingClientRect();
            const x = event.clientX - rect.left;
            const y = event.clientY - rect.top;
            return {
                px: x, py: y,
                x: chart.scales.x.getValueForPixel(x),
                y: chart.scales.y.getValueForPixel(y),
            };
        }

        onPointerDown(event) {
            if (!this.getChart()) return;
            this.canvas.setPointerCapture(event.pointerId);
            this.pointers.set(event.pointerId, event);
            if (this.pointers.size === 1) {
                const at = this.plotValue(event);
                this.gesture = this.selectMode
                    ? {kind: 'marquee', from: at, to: at}
                    : {kind: 'pan', from: at};
                if (this.selectMode) this.showMarquee(at, at);
            } else if (this.pointers.size === 2) {
                this.hideMarquee();
                this.gesture = {kind: 'pinch', start: this.pinchState()};
            }
        }

        pinchState() {
            const [a, b] = Array.from(this.pointers.values());
            const chart = this.getChart();
            const rect = this.canvas.getBoundingClientRect();
            const ax = a.clientX - rect.left, bx = b.clientX - rect.left;
            const ay = a.clientY - rect.top, by = b.clientY - rect.top;
            return {
                spanX: Math.max(Math.abs(ax - bx), 1),
                spanY: Math.max(Math.abs(ay - by), 1),
                midX: chart.scales.x.getValueForPixel((ax + bx) / 2),
                midY: chart.scales.y.getValueForPixel((ay + by) / 2),
                frequency: Object.assign({}, this.frequency),
                level: this.level
                    ? Object.assign({}, this.level)
                    : {min: chart.scales.y.min, max: chart.scales.y.max},
            };
        }

        onPointerMove(event) {
            if (!this.pointers.has(event.pointerId) || !this.gesture) return;
            this.pointers.set(event.pointerId, event);
            if (this.gesture.kind === 'pan') {
                const at = this.plotValue(event);
                if (!at || !Number.isFinite(at.x) || at.x <= 0) return;
                this.panFrequency(this.gesture.from.x / at.x);
                const chart = this.getChart();
                // Re-read after the frequency pan: the y pixel mapping is unchanged.
                const grabbed = chart.scales.y.getValueForPixel(this.gesture.from.py);
                this.panLevel(grabbed - at.y);
                this.gesture.from = this.plotValue(event);
            } else if (this.gesture.kind === 'pinch' && this.pointers.size >= 2) {
                const now = this.pinchState();
                const start = this.gesture.start;
                const fx = start.spanX / now.spanX;
                const fy = start.spanY / now.spanY;
                const f = start.frequency;
                this.setFrequency(start.midX * Math.pow(f.min / start.midX, fx),
                                  start.midX * Math.pow(f.max / start.midX, fx));
                const l = start.level;
                this.setLevel(start.midY + (l.min - start.midY) * fy,
                              start.midY + (l.max - start.midY) * fy);
            } else if (this.gesture.kind === 'marquee') {
                this.gesture.to = this.plotValue(event);
                this.showMarquee(this.gesture.from, this.gesture.to);
            }
        }

        onPointerUp(event) {
            this.pointers.delete(event.pointerId);
            const gesture = this.gesture;
            if (gesture && gesture.kind === 'marquee' && this.pointers.size === 0) {
                this.hideMarquee();
                const {from, to} = gesture;
                if (Math.abs(from.px - to.px) > 12 || Math.abs(from.py - to.py) > 12) {
                    if (Math.abs(from.px - to.px) > 12) this.setFrequency(from.x, to.x);
                    if (Math.abs(from.py - to.py) > 12) this.setLevel(from.y, to.y);
                }
            }
            if (this.pointers.size === 0) this.gesture = null;
            else if (this.pointers.size === 1) this.gesture = {kind: 'pan', from: null};
            if (this.gesture && this.gesture.kind === 'pan' && !this.gesture.from) {
                const remaining = Array.from(this.pointers.values())[0];
                this.gesture.from = this.plotValue(remaining);
            }
        }

        onWheel(event) {
            if (!this.getChart()) return;
            event.preventDefault();
            const at = this.plotValue(event);
            const factor = event.deltaY < 0 ? 0.82 : 1 / 0.82;
            if (event.shiftKey) this.zoomLevel(factor, at.y);
            else if (event.ctrlKey || event.metaKey) {
                this.zoomFrequency(factor, at.x);
                this.zoomLevel(factor, at.y);
            } else this.zoomFrequency(factor, at.x);
        }

        setSelectMode(enabled) {
            this.selectMode = !!enabled;
            if (this.canvas) this.canvas.style.cursor = enabled ? 'crosshair' : 'grab';
            this.hideMarquee();
        }

        showMarquee(from, to) {
            if (!this.marquee) {
                this.marquee = document.createElement('div');
                this.marquee.className = 'marquee';
                this.container.appendChild(this.marquee);
            }
            const left = Math.min(from.px, to.px);
            const top = Math.min(from.py, to.py);
            this.marquee.style.display = 'block';
            this.marquee.style.left = left + 'px';
            this.marquee.style.top = top + 'px';
            this.marquee.style.width = Math.abs(from.px - to.px) + 'px';
            this.marquee.style.height = Math.abs(from.py - to.py) + 'px';
        }

        hideMarquee() {
            if (this.marquee) this.marquee.style.display = 'none';
        }
    }

    root.FilterResponseView = {
        ResponseView,
        DEFAULT_FREQUENCY_RANGE,
        FREQUENCY_PRESETS,
        MIN_AUTOFIT_SPAN_DB,
        frequencyTicks,
        formatHz,
    };
}(typeof window !== 'undefined' ? window : this));
