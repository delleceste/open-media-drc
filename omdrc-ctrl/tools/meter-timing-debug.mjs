#!/usr/bin/env node
// Node >= 22; adb on PATH. No npm dependencies. Only --acoustic plays sound.
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function summarize(recording) {
    const rows = recording.events;
    const end = rows.find(e => e.event === 'faultEnd');
    const begin = rows.find(e => e.event === 'faultStart');
    const mode = begin?.mode || 'vu';
    const draws = rows.filter(e => e.event === 'draw' && e.mode === mode && e.usable);
    const before = draws.filter(e => !begin || e.t < begin.t);
    // Allow 3 s for reconnection and the configured drawing wait.
    const after = draws.filter(e => end && e.t > end.t + 3000);
    const stats = values => {
        const a = values.map(e => e.lateMs).filter(Number.isFinite).sort((a,b) => a-b);
        return { samples: a.length, p95LateMs: a.length ? a[Math.ceil(a.length * .95) - 1] : null };
    };
    const baseline = stats(before), recovered = stats(after);
    const interrupted = rows.some(e => e.event === 'context' || (e.event === 'visibility' && e.hidden));
    const exercised = begin?.kind === 'hold' ? end?.queued >= 5
        : rows.some(e => e.event === 'injectedDrop' && e.mode === mode);
    let verdict = 'inconclusive';
    if (begin && end && exercised && !recording.omitted && !interrupted && baseline.samples >= 10 && recovered.samples >= 10)
        verdict = recovered.p95LateMs <= Math.max(100, baseline.p95LateMs + 50) ? 'delivery-recovered' : 'delivery-late';
    return { verdict, mode, baseline, recovered, counters: recording.counters,
        note: 'Delivery timing only; acoustic synchronization requires microphone checks. Age is relative to best observed transit, not absolute network latency.' };
}

async function main() {
    const args = process.argv.slice(2);
    if (args.includes('--help')) {
        console.log('Usage: node meter-timing-debug.mjs [--serial SERIAL] [--seconds 30] [--fault hold|drop] [--mode vu-clip|music-clip|vu|music|precision] [--acoustic] [--out timing.json]\nKeep OMDRC visible. --fault affects meter data only, for 2.2 s after 5 s baseline.\n--acoustic stops music and plays click tests before/after capture; measures without saving calibration.');
        return;
    }
    const options = {};
    for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        if (arg === '--acoustic') options.acoustic = true;
        else if (['--serial','--seconds','--fault','--mode','--out'].includes(arg) && args[i+1] && !args[i+1].startsWith('--')) options[arg.slice(2)] = args[++i];
        else throw new Error('Unknown or incomplete option: ' + arg);
    }
    const seconds = Number(options.seconds || 30), mode = options.mode || 'vu-clip';
    if (!Number.isInteger(seconds) || seconds < 15 || seconds > 480) throw new Error('--seconds must be 15–480');
    if (!['vu', 'music', 'precision', 'vu-clip', 'music-clip', 'precision-clip'].includes(mode)) throw new Error('Invalid --mode');
    if (options.fault && !['hold', 'drop'].includes(options.fault)) throw new Error('Invalid --fault');
    const adb = (...a) => execFileSync('adb', [...(options.serial ? ['-s', options.serial] : []), ...a], { encoding:'utf8', timeout:15000 }).trim();
    const pid = adb('shell', 'pidof', 'com.omdrc.widget').split(/\s+/)[0];
    if (!/^\d+$/.test(pid)) throw new Error('Open the debug OMDRC app on the connected phone');
    const port = adb('forward', 'tcp:0', 'localabstract:webview_devtools_remote_' + pid);
    let ws, recordingStarted = false;
    const pending = new Map(); let id = 0;
    const call = (method, params = {}) => new Promise((resolve, reject) => {
        const n = ++id;
        const timer = setTimeout(() => { pending.delete(n); reject(new Error('DevTools timeout: ' + method)); }, 65000);
        pending.set(n, { resolve, reject, timer });
        ws.send(JSON.stringify({ id:n, method, params }));
    });
    const evaluate = async expression => {
        const r = await call('Runtime.evaluate', { expression, awaitPromise:true, returnByValue:true });
        if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
        return r.result?.value;
    };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const output = { acoustic: {} };
    try {
        const targets = await (await fetch(`http://127.0.0.1:${port}/json`, { signal:AbortSignal.timeout(10000) })).json();
        const visible = t => { try { return JSON.parse(t.description).visible ? 1 : 0; } catch { return 0; } };
        const candidates = targets.sort((a,b) => visible(b)-visible(a)).filter(t => t.type === 'page' && /\/(?:k|kiosk)(?:\/|\?|$)/.test(t.url));
        let ready = false;
        for (const target of candidates) {
            const address = new URL(target.webSocketDebuggerUrl); address.hostname = '127.0.0.1'; address.port = port;
            ws = new WebSocket(address);
            await new Promise((resolve,reject) => {
                const timer = setTimeout(() => reject(new Error('WebView connection timeout')), 10000);
                ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, {once:true});
                ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('WebView connection failed')); }, {once:true});
            });
            ws.addEventListener('message', ev => {
                const msg = JSON.parse(ev.data), p = pending.get(msg.id);
                if (!p) return;
                pending.delete(msg.id); clearTimeout(p.timer);
                if (msg.error) p.reject(new Error(msg.error.message)); else p.resolve(msg.result);
            });
            ws.addEventListener('close', () => { for (const p of pending.values()) { clearTimeout(p.timer); p.reject(new Error('WebView disconnected')); } pending.clear(); });
            ready = await evaluate('!!window.K?.timingDebug && !document.hidden');
            if (ready) break;
            await new Promise(resolve => { ws.addEventListener('close',resolve,{once:true}); ws.close(); });
        }
        if (!ready) throw new Error('Open the updated kiosk dashboard in the foreground; reload if needed');
        await evaluate(`K.timingDebug.start(${seconds + 100})`); recordingStarted = true;
        const acoustic = async label => {
            console.log(`${label}: microphone click check (music will stop)`);
            output.acoustic[label] = await evaluate('(async () => { const result = await K.sync.calibrate({seconds:14, verify:true, why:"adb-diagnostics"}); return {result, log:K.sync.lastLog()}; })()');
        };
        if (options.acoustic) await acoustic('before');
        console.log(`Recording ${seconds}s; ${options.fault ? options.fault + ' fault after 5s' : 'no injected fault'}.`);
        await sleep(5000);
        if (options.fault) await evaluate(`K.timingDebug.inject(${JSON.stringify({kind:options.fault,mode,ms:2200})})`);
        await sleep((seconds-5)*1000);
        if (options.acoustic) await acoustic('after');
        output.recording = await evaluate('K.timingDebug.stop()'); recordingStarted = false;
        output.summary = summarize(output.recording);
        const before = output.acoustic.before?.result, after = output.acoustic.after?.result;
        if (options.acoustic) output.acoustic.comparison = before?.ok && after?.ok && before.context === after.context
            ? { beforeResidualMs:before.lagMs, afterResidualMs:after.lagMs, driftMs:after.lagMs-before.lagMs }
            : { verdict:'inconclusive', reason:'Both microphone measurements must succeed in the same configuration' };
        console.log(JSON.stringify(output.summary, null, 2));
        if (options.acoustic) console.log(JSON.stringify(output.acoustic.comparison, null, 2));
        if (output.summary.verdict === 'delivery-late') process.exitCode = 1;
    } catch (e) {
        output.error = e.message;
        throw e;
    } finally {
        if (recordingStarted && ws?.readyState === WebSocket.OPEN) {
            try { output.recording = await evaluate('K.timingDebug.stop()'); } catch {}
        }
        ws?.close();
        try { adb('forward','--remove','tcp:' + port); } catch {}
        const path = options.out || 'meter-timing.json';
        writeFileSync(path, JSON.stringify(output,null,2) + '\n');
        console.log('Saved ' + path);
    }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
    main().catch(e => { console.error(e.message); process.exitCode = 1; });
