import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { summarize } from '../omdrc-ctrl/tools/meter-timing-debug.mjs';
let now = 10000, seq = 0;
const timers = new Map(), streams = [], drawn = [];
const timeout = (fn, ms = 0) => { const id = ++seq; timers.set(id, {fn, at:now+ms}); return id; };
const advance = ms => {
    const until = now+ms;
    for (;;) {
        const next = [...timers].filter(([,t]) => t.at <= until).sort((a,b) => a[1].at-b[1].at)[0];
        if (!next) break;
        timers.delete(next[0]); now=next[1].at; next[1].fn();
    }
    now=until;
};
let delay = 200;
const K = { state:{spectrum:{enabled:true}}, sync:{delayMs:()=>delay} };
const c = vm.createContext({K, window:{}, console:{info(){}}, performance:{now:()=>now}, Date:{now:()=>now},
    document:{hidden:false,addEventListener(){}}, setTimeout:timeout,clearTimeout:id=>timers.delete(id),
    setInterval:()=>0,clearInterval(){}, requestAnimationFrame:fn=>timeout(fn,16),cancelAnimationFrame:id=>timers.delete(id),
    EventSource:class {constructor(){streams.push(this);}close(){}},encodeURIComponent});
vm.runInContext(fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/timing-debug.js','utf8'),c);
const src=fs.readFileSync('omdrc-ctrl/src/kiosk/static/core.js','utf8');
vm.runInContext(src.slice(src.indexOf('K.streams ='),src.indexOf('// Code that needs the boot config')),c);
K.timingDebug.start(60);
K.streams.open('vu',d=>drawn.push(d.id));
let id=0;
const send=()=>streams.at(-1).onmessage({data:JSON.stringify({id:++id,sent:now-20,published:now-20,ok:true,state:'running',vu:{left_peak:-20}})});
const run=ms=>{for(let i=0;i<ms/40;i++){send();advance(40);}};
run(5000);
K.timingDebug.inject({kind:'hold',ms:2200});
run(9000);
const r=K.timingDebug.stop();
assert.ok(r.counters['vu:stale']>=5);
assert.equal(streams.length,2);
assert.equal(summarize(r).verdict,'delivery-recovered');
assert.equal(summarize({...r,events:r.events.map(e=>e.event==='draw' && e.t>10000 ? {...e,lateMs:500}:e)}).verdict,'delivery-late');
assert.equal(r.active,false);
assert.ok(drawn.at(-1)>300);
assert.equal(summarize({...r, events:r.events.map(e=>({...e,usable:false}))}).verdict,'inconclusive');
// No baseline or foreground loss must never earn a recovery verdict.
assert.equal(summarize({...r,events:r.events.filter(e=>e.t>=5000)}).verdict,'inconclusive');
assert.equal(summarize({...r,events:[...r.events,{event:'visibility',hidden:true}]}).verdict,'inconclusive');
K.timingDebug.start();
K.timingDebug.inject({kind:'drop',ms:500,every:2});
run(600);
assert.ok(K.timingDebug.snapshot().counters['vu:injectedDrop']>0);
K.timingDebug.stop();
// Watchdog releases a held queue even if the controlling ADB process disappears.
K.timingDebug.start(1);
K.timingDebug.inject({kind:'hold',ms:3000});
send();advance(1100);
assert.equal(K.timingDebug.snapshot().active,false);
assert.ok(K.timingDebug.snapshot().events.some(e=>e.event==='faultEnd'));
// Changing waits cannot cause an older pending frame to overwrite a newer draw.
K.timingDebug.start();
delay=1000;send();const old=id;
advance(40);delay=0;send();advance(1100);
assert.ok(!drawn.includes(old));
assert.ok(K.timingDebug.snapshot().counters['vu:outOfOrder']>=1);
// Pending old RAF is canceled on replacement, and fresh frames still render.
delay=0;send();
for(let i=0;i<5;i++) streams.at(-1).onmessage({data:JSON.stringify({sent:now-2020})});
send();const fresh=id;advance(16);
assert.equal(drawn.at(-1),fresh);
K.timingDebug.stop();
console.log('Timing diagnostics: burst recovery, loss, watchdog, ordering and inconclusive evidence passed');
