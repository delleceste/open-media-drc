/* Bass depth: the background analysis of the track playing as a timeline of
 * bass passages, and three buttons to say how deep the basses sound right now.
 * The card itself is widgets/bass-depth.js, shared with the desktop panel. */
(() => {
'use strict';
const { h } = K;

const P = { id: 'bass', label: 'Bass depth', title: 'Bass depth', card: null };

P.mount = el => {
    P.el = el;
    const root = h('div', {});
    el.append(K.card('Bass depth', root));
    P.card = window.BassDepth.mount(root);
};
P.show = () => P.card && P.card.start();
P.hide = () => P.card && P.card.stop();

K.registerPage(P);
})();
