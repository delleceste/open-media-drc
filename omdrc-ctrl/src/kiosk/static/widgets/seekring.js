/* The seek ring on a cover (Now upright, the Qobuz page's full player).
 *
 * Two steps, so a stray touch on the cover never moves the music: the first touch
 * is held, undecided, until it lifts or moves. Lifted without moving (a tap), it
 * brings up a ring inscribed in the cover, 12 o'clock the start of the track,
 * clockwise to the end, the knob where it is now -- or, if the ring was already up,
 * puts it away. Moved instead (a swipe, from any point inside the cover, down or
 * otherwise) it never shows the ring; it tracks as a downward swipe-to-minimize
 * instead (onPull/onMinimize below), putting an already-shown ring away rather than
 * dragging it along. While the ring is up, a slide that *starts on the ring* moves
 * the knob round it (it stops at the start and the end, it never jumps across) and
 * lifting the finger seeks there. Untouched, the ring goes away by itself after a
 * few seconds. flash() shows it for a moment and fades it out, so the user knows it
 * is there.
 *
 *   new K.SeekRing(box, { usable(), elapsed(), duration(), seek(seconds) })
 * Optional onMinimize is called on a downward swipe past a threshold, and
 * onPull(distance) moves the player with the finger as that swipe happens;
 * a tap always just reveals (or puts away) the ring, never minimizes.
 * `box` is the cover's element (position: relative); the ring is added to it. */
(() => {
const h = K.h;
const RING_R = 44;            // in the ring's 100 x 100 viewBox
const RING_GRAB = 0.11;       // how far off the ring a slide may start, as a share of the cover's width
const RING_IDLE_MS = 4000;    // the ring goes away this long after the last touch
const RING_C = 2 * Math.PI * RING_R;
const SVG = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs) => { const e = document.createElementNS(SVG, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

K.SeekRing = class {
    constructor(box, o) {
        this.box = box; this.o = o;
        this.el = h('div', { class: 'seek-ring', hidden: true });
        const s = svg('svg', { viewBox: '0 0 100 100' });
        s.append(svg('circle', { class: 'sr-track', cx: 50, cy: 50, r: RING_R }));
        this.arc = svg('circle', { class: 'sr-arc', cx: 50, cy: 50, r: RING_R, transform: 'rotate(-90 50 50)', 'stroke-dasharray': `0 ${RING_C}` });
        this.knob = svg('circle', { class: 'sr-knob', cx: 50, cy: 50 - RING_R, r: 4.2 });
        s.append(this.arc, this.knob);
        this.time = h('div', { class: 'sr-time' });
        this.el.append(s, this.time);
        box.append(this.el);
        this.wire();
    }

    get shown() { return !this.el.hidden; }
    frac() { return this.o.elapsed() / this.o.duration(); }
    show() { this.el.classList.remove('fading'); this.el.hidden = false; this.paint(this.frac()); }
    hide() { clearTimeout(this.timer); this.el.classList.remove('fading'); this.el.hidden = true; }
    hideLater(ms) { clearTimeout(this.timer); this.timer = setTimeout(() => this.hide(), ms); }

    // Shared tap rhythm for both Now playing views. A new sequence only reveals the ring.
    step(direction, songid) {
        if (!this.o.usable()) return null;
        const now = performance.now(), previous = this.taps;
        const repeat = previous && previous.direction === direction && previous.songid === songid && now - previous.at < RING_IDLE_MS;
        const interval = previous ? now - previous.at : 0;
        const amount = repeat && previous.count > 1 ? 5 * Math.max(1, Math.min(8, 600 / Math.max(75, interval))) : 5;
        this.taps = { direction, songid, at: now, count: repeat ? previous.count + 1 : 1 };
        this.show(); this.hideLater(RING_IDLE_MS);
        return repeat ? K.clamp(this.o.elapsed() + direction * amount, 0, Math.max(0, this.o.duration() - 1)) : null;
    }

    // shown for `ms`, then faded out (kiosk.css) -- untouched meanwhile
    flash(ms = 1000) {
        if (!this.o.usable()) return;
        this.show();
        clearTimeout(this.timer);
        this.timer = setTimeout(() => {
            this.el.classList.add('fading');
            this.timer = setTimeout(() => this.hide(), 600);
        }, ms);
    }

    paint(f) {
        const d = this.o.duration();
        this.arc.setAttribute('stroke-dasharray', `${(f * RING_C).toFixed(2)} ${RING_C}`);
        const a = f * 2 * Math.PI;
        this.knob.setAttribute('cx', (50 + RING_R * Math.sin(a)).toFixed(2));
        this.knob.setAttribute('cy', (50 - RING_R * Math.cos(a)).toFixed(2));
        this.time.textContent = d > 0 ? `${K.fmtClock(f * d)} / ${K.fmtClock(d)}` : '';
    }

    destroy() {
        this.hide(); this.taps = null;
        try { window.OmdrcApp && window.OmdrcApp.setPageScrolled(false); } catch {}
    }

    wire() {
        const box = this.box;
        box.addEventListener('contextmenu', e => e.preventDefault());
        let g = null;
        // is the finger on the ring (the circle's radius, give or take RING_GRAB)?
        const onRing = e => {
            const r = box.getBoundingClientRect();
            const d = Math.hypot(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2));
            return Math.abs(d - r.width * RING_R / 100) <= Math.max(18, r.width * RING_GRAB);
        };
        const fracAt = e => {
            const r = box.getBoundingClientRect();
            const a = Math.atan2(e.clientX - (r.left + r.width / 2), -(e.clientY - (r.top + r.height / 2)));
            return (a / (2 * Math.PI) + 1) % 1;
        };
        box.addEventListener('pointerdown', e => {
            if (e.button !== 0) return;
            if (!this.o.usable()) return;
            e.preventDefault();
            try { window.OmdrcApp && window.OmdrcApp.setPageScrolled(true); } catch {}   // not pull-to-reload
            const f = this.frac();
            if (this.shown && !this.el.classList.contains('fading') && onRing(e)) {
                // the ring is up and the finger lands on it: a slide drags the knob
                g = { id: e.pointerId, x: e.clientX, y: e.clientY, f, moved: false };
                this.el.classList.add('active');           // drawn thick only while the finger is on it
                try { box.setPointerCapture(e.pointerId); } catch {}
                clearTimeout(this.timer);
                this.paint(g.f);
                return;
            }
            // anywhere else on the cover: wait for pointerup/pointermove to tell a tap
            // (reveals, or puts away, the ring) from a swipe down (minimizes instead;
            // the ring never appears for it, from any point inside the cover)
            g = { id: e.pointerId, x: e.clientX, y: e.clientY, cover: true, moved: false, dy: 0,
                wasShown: this.shown && !this.el.classList.contains('fading') };
            try { box.setPointerCapture(e.pointerId); } catch {}
        });
        box.addEventListener('pointermove', e => {
            if (!g || e.pointerId !== g.id) return;
            if (!g.moved && Math.hypot(e.clientX - g.x, e.clientY - g.y) < 8) return;
            g.moved = true;
            if (g.cover) {
                if (g.wasShown) this.hide();   // a swipe puts a shown ring away, it never drags along with it
                g.dy = Math.max(0, e.clientY - g.y);
                if (this.o.onPull) this.o.onPull(g.dy);
                return;
            }
            // the nearest of a, a - 1, a + 1 to where the knob is: no jump across 12 o'clock
            const a = fracAt(e);
            const near = [a - 1, a, a + 1].reduce((b, c) => Math.abs(c - g.f) < Math.abs(b - g.f) ? c : b);
            g.f = K.clamp(near, 0, 1);
            this.paint(g.f);
        });
        const end = e => {
            try { window.OmdrcApp && window.OmdrcApp.setPageScrolled(false); } catch {}
            if (!g || e.pointerId !== g.id) return;
            const d = g; g = null;
            this.el.classList.remove('active');
            if (d.cover) {
                if (!d.moved) {   // a tap: reveal the ring, or put an already-shown one away
                    if (d.wasShown) this.hide(); else { this.show(); this.hideLater(RING_IDLE_MS); }
                } else if (e.type === 'pointerup' && d.dy > 40) this.o.onMinimize && this.o.onMinimize();
                else if (this.o.onPull) this.o.onPull(0);
                return;
            }
            if (d.moved && e.type === 'pointerup') this.o.seek(d.f * this.o.duration());
            this.hideLater(d.moved ? 1200 : RING_IDLE_MS);
        };
        box.addEventListener('pointerup', end);
        box.addEventListener('pointercancel', end);
    }
};

// A button centred on a cover box (now.js, qobuz.js) that opens the cover full
// screen, with its own seek ring; a top-right X closes it.
//   K.coverMaxButton(() => src)   -> <button class="cover-max-btn">, wire it up yourself
//   K.openCoverMax(src, { usable(), elapsed(), duration(), seek(seconds) })
K.coverMaxButton = (getSrc, o) => h('button', { type: 'button', class: 'cover-max-btn', title: 'View the cover full screen',
    onpointerdown: e => e.stopPropagation(), onclick: e => { e.stopPropagation(); const src = getSrc(); if (src) K.openCoverMax(src, o); } },
    K.tIcon('maximize'));

K.openCoverMax = (src, o) => {
    const art = h('div', { class: 'cover-max-art' }, h('img', { src, alt: '' }));
    const close = () => { ring.destroy(); document.removeEventListener('keydown', onKey); scrim.remove(); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    const scrim = h('div', { class: 'scrim cover-max', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'cover-max-top' },
            h('button', { type: 'button', class: 'btn cover-max-close', title: 'Close', onclick: close }, K.tIcon('close'))),
        art);
    const ring = new K.SeekRing(art, o);
    document.addEventListener('keydown', onKey);
    document.getElementById('overlay-root').append(scrim);
    ring.flash();
};
})();
