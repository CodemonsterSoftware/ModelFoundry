// Kiri:Moto engine running inside a worker_threads Worker.
// server.js owns HTTP, the request queue and the per-slice deadline; on a
// timeout it terminate()s this thread (which also kills CPU-bound loops) and
// spawns a fresh one.
// Protocol: -> {id, stl: ArrayBuffer, device, process}
//           <- {ready: true} once, then {id, ok, filament_mm, time_s, breakdown_mm} | {id, ok: false, error}
const { parentPort } = require('worker_threads');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const PACK = process.env.KIRI_PACK || '/grid/src/pack';

// --- browser shims: the engine bundle references DOM globals at module scope ---
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.location = {
    search: '', hash: '', host: 'localhost', hostname: 'localhost', port: 0,
    protocol: 'http:', origin: 'http://localhost', pathname: '/', href: 'http://localhost/',
};
const fakeCtx = {
    clearRect() { }, fillText() { }, strokeRect() { },
    measureText: () => ({ width: 0 }), getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    font: '', fillStyle: '', textAlign: '', textBaseline: '', lineWidth: 1,
};
globalThis.document = {
    readyState: 'complete',
    getElementById: () => null,
    createElement: (tag) => tag === 'canvas' ? {
        width: 0, height: 0,
        getContext: () => fakeCtx,
        toDataURL: () => 'data:,',
    } : { appendChild() { }, addEventListener() { }, style: {} },
    createTextNode: () => ({}),
    addEventListener() { },
    removeEventListener() { },
};

// repo template JSONs have trailing commas — strip for strict parse
function looseJson(file) {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/,\s*([}\]])/g, '$1'));
}

function merge(base, over) {
    const out = Object.assign({}, base);
    for (const [k, v] of Object.entries(over || {})) {
        if (v && typeof v === 'object' && !Array.isArray(v) && typeof out[k] === 'object') {
            out[k] = merge(out[k], v);
        } else {
            out[k] = v;
        }
    }
    return out;
}

// Per-feature extrusion from Kiri's "; feature <name>" gcode comments.
// e-retract/e-engage lines are skipped entirely: a retract is attributed to
// the window it lands in, which skews small per-layer windows (can go
// negative). Skipped pairs cancel, so the sum still == print.distance.
// G92 sets the E origin exactly; extrusion before the first feature comment
// counts as 'other' so nothing leaks into the first real feature.
const FEATURE_GROUP = {
    shells: 'shell', brim: 'shell',
    'sparse infill': 'infill', 'solid fill': 'infill',
    support: 'support',
};
function featureBreakdown(gcode) {
    const by = {};
    let lastE = 0, group = 'other';
    for (const line of gcode.split(/\r?\n/)) {
        const f = line.match(/^;\s*feature\s+(.+)$/);
        if (f) { group = FEATURE_GROUP[f[1].trim()] || 'other'; continue; }
        if (/^G92/.test(line)) {
            const m = line.match(/\bE(-?[0-9.]+)/);
            if (m) lastE = parseFloat(m[1]);
            continue;
        }
        if (/e-(retract|engage)/.test(line)) continue;
        const m = line.match(/\bE(-?[0-9.]+)/);
        if (!m) continue;
        const e = parseFloat(m[1]);
        by[group] = (by[group] || 0) + (e - lastE);
        lastE = e;
    }
    for (const k of Object.keys(by)) by[k] = Math.round(by[k] * 100) / 100;
    return by;
}

async function main() {
    await import(pathToFileURL(path.join(PACK, 'kiri-work.js')).href);
    const { newEngine } = await import(pathToFileURL(path.join(PACK, 'kiri-eng.js')).href);

    // engine<->worker message round-trip in one thread:
    // client sends -> fake worker postMessage -> worker dispatch;
    // worker replies -> global postMessage -> active client onmessage.
    let activeWorker = null;
    class FakeWorker {
        postMessage(msg) {
            setImmediate(() => self.kiri_worker.dispatch.onmessage({ data: msg }));
        }
    }
    self.createWorker = () => { const w = new FakeWorker(); activeWorker = w; return w; };
    globalThis.postMessage = (msg) => {
        setImmediate(() => activeWorker && activeWorker.onmessage({ data: msg }));
    };

    const baseDevice = looseJson('/grid/src/cli/kiri-fdm-device.json');
    const baseProcess = looseJson('/grid/src/cli/kiri-fdm-process.json');

    async function runSlice(stl, device, processDef) {
        if (!stl || !stl.byteLength) throw new Error('empty stl');
        const engine = newEngine();
        await engine.parse(stl);
        engine.setDevice(merge(baseDevice, device));
        engine.setProcess(merge(baseProcess, processDef));
        await engine.slice();
        await engine.prepare();
        const gcode = await engine.export(); // finalizes print stats

        const print = self.kiri_worker.current.print;
        return {
            filament_mm: Math.round(print.distance * 100) / 100,
            time_s: Math.round(print.time),
            breakdown_mm: featureBreakdown(gcode),
        };
    }

    // server.js sends one job at a time (serialized queue): worker state is thread-global
    parentPort.on('message', async (job) => {
        try {
            const result = await runSlice(job.stl, job.device, job.process);
            parentPort.postMessage(Object.assign({ id: job.id, ok: true }, result));
        } catch (e) {
            parentPort.postMessage({ id: job.id, ok: false, error: String((e && e.message) || e) });
        }
    });
    parentPort.postMessage({ ready: true });
}

main().catch((e) => {
    console.error('slicer engine failed to start', e);
    process.exit(1);
});
