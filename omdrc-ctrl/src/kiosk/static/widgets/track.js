/* The current track, normalised from whichever source knows it: the renderer's
 * status file (/qconnect/status: cover art, edition, elapsed/duration) or, when
 * no renderer is driving MPD, MPD itself (/mpd/info). */
(() => {
'use strict';

// "[playing] Can - Arles 75 Eins · LIVE IN ARLES 1975"  →  artist/title/album
function parseLine1(line1) {
    let s = String(line1 || '').replace(/^\s*\[[^\]]*\]\s*/, '').trim();
    if (!s || s === '—') return null;
    let album = '';
    const dot = s.split(' · ');
    if (dot.length > 1) { s = dot[0]; album = dot.slice(1).join(' · '); }
    const dash = s.indexOf(' - ');
    return dash > 0
        ? { artist: s.slice(0, dash), title: s.slice(dash + 3), album }
        : { artist: '', title: s, album };
}

K.fetchTrack = async () => {
    const q = await K.api('/qconnect/status', { timeout: 6000 });
    const parsed = q && q.ok ? parseLine1(q.line1) : null;
    if (parsed) {
        const state = q.playback_state === 'play' ? 'play' : q.playback_state === 'pause' ? 'pause' : 'stop';
        return {
            ok: true, from: 'renderer', ...parsed,
            edition: state === 'stop' ? '' : (q.edition || ''),
            format: q.line2 || '', state,
            elapsed: Number(q.elapsed), duration: Number(q.duration),
            art: state === 'stop' ? '' : (q.art || ''),
            activity: Array.isArray(q.events) ? q.events : (q.line3 ? [q.line3] : []),
            phase: q.state || '',
        };
    }
    const m = await K.api('/mpd/info', { timeout: 6000 });
    if (m && m.ok && m.running) {
        const state = m.state === 'playing' ? 'play' : m.state === 'paused' ? 'pause' : 'stop';
        const rate = m.sample_rate ? `${m.bit_depth || '?'} bit / ${+(m.sample_rate / 1000).toFixed(1)} kHz / ${m.channels === 2 ? 'stereo' : (m.channels || '?') + ' ch'}` : '';
        const split = parseLine1(m.song) || {};      // MPD reports "Artist - Title" as one string
        return {
            ok: true, from: 'mpd', artist: m.artist || split.artist || '',
            title: m.title || split.title || m.song || '', album: m.album || '',
            edition: '', format: rate, state, elapsed: NaN, duration: NaN, art: '', activity: [], phase: '',
        };
    }
    return { ok: false, from: '', artist: '', title: '', album: '', edition: '', format: '', state: 'stop', elapsed: NaN, duration: NaN, art: '', activity: [], phase: '' };
};

// Colour a "24 bit / 96 kHz / stereo" line the way the desktop panel does.
K.formatColor = line => {
    const m = String(line || '').match(/(\d+)\s*bit.*?([\d.]+)\s*kHz/i);
    if (!m) return '';
    const bits = parseInt(m[1], 10), khz = parseFloat(m[2]);
    if (bits >= 24 && khz >= 176) return 'var(--green)';
    if (bits >= 24 && khz >= 88) return '#79c0ff';
    if (bits >= 24) return '#e3b341';
    if (khz >= 48) return '#d2a8ff';
    return '';
};
})();
