/* The separate omdrcvideo service, displayed inside the kiosk when available. */
(() => {
'use strict';
const { h } = K;
const P = { id: 'video', label: 'Video', title: 'Video', available: false, visible: false,
    optional: () => P.available || P.visible };

P.mount = el => {
    el.classList.add('video-page-body');
    P.frame = h('iframe', { class: 'video-frame', title: 'Video remote' });
    P.offline = h('div', { class: 'video-offline', hidden: true },
        h('p', {}, 'The video remote is unavailable.'),
        h('button', { class: 'btn', type: 'button', onclick: () => P.check() }, 'Retry'));
    el.append(P.frame, P.offline, h('div', { class: 'video-edge l' }), h('div', { class: 'video-edge r' }));
    P.paint();
};
P.paint = () => {
    if (!P.frame) return;
    P.frame.hidden = !P.available;
    P.offline.hidden = P.available;
    if (P.available && P.visible && !P.frame.hasAttribute('src')) {
        P.frame.src = `${location.protocol}//${location.hostname}:9080/`;
    } else if (!P.available) {
        P.frame.removeAttribute('src');
    }
};
P.show = () => { P.visible = true; P.paint(); };
P.hide = () => {
    P.visible = false;
    if (!P.available) setTimeout(() => K.refreshPages(), 0);
};
P.setAvailable = available => {
    if (P.available === available) return;
    P.available = available;
    P.paint();
    if (K.allPages) K.refreshPages();
};
P.check = async () => {
    const result = await K.api('/k/api/video', { timeout: 2000 });
    P.setAvailable(result.ok === true && result.available === true);
};
setInterval(() => { if (!document.hidden) P.check(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) P.check(); });
K.video = P;
K.registerPage(P);
})();
