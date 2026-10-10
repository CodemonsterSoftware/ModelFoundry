// Headless Kiri:Moto slicer sidecar.
// POST /slice {stl: base64, device: {...}, process: {...}} ->
//   {ok, filament_mm, time_s, breakdown_mm: {shell, infill, support, other}}
// GET  /health -> {ok: true, ready, busy}
//
// The engine runs in a worker_threads Worker (engine-worker.js). Each slice
// gets a deadline (SLICE_TIMEOUT_MS); on timeout or engine crash the worker is
// terminated and replaced, so one pathological mesh cannot wedge the queue.
const http = require('http');
const path = require('path');
const { Worker } = require('worker_threads');

const PORT = parseInt(process.env.SLICER_PORT || '8090', 10);
const SLICE_TIMEOUT_MS = parseInt(process.env.SLICE_TIMEOUT_MS || '600000', 10);
const MAX_BODY = 64 * 1024 * 1024;

let worker = null;
let engineReady = false;
let everReady = false;
let readyWaiters = [];
let pending = null; // {id, resolve, reject} for the in-flight slice
let busy = false;
let seq = 0;

function whenReady() {
    return engineReady ? Promise.resolve() : new Promise((resolve) => readyWaiters.push(resolve));
}

function spawn() {
    const w = new Worker(path.join(__dirname, 'engine-worker.js'));
    worker = w;
    engineReady = false;
    w.on('message', (msg) => {
        if (w !== worker) return;
        if (msg.ready) {
            engineReady = everReady = true;
            readyWaiters.splice(0).forEach((r) => r());
            console.log('slicer engine ready');
            return;
        }
        if (pending && msg.id === pending.id) pending.resolve(msg);
    });
    w.on('error', (e) => console.error('slicer engine error', e));
    w.on('exit', (code) => {
        if (w !== worker) return; // replaced on purpose (timeout)
        if (!everReady) {
            console.error(`slicer engine failed to start (exit ${code})`);
            process.exit(1);
        }
        console.error(`slicer engine exited (code ${code}); respawning`);
        if (pending) pending.reject(new Error(`slicer engine crashed (exit ${code})`));
        spawn();
    });
}

function restart() {
    const old = worker;
    spawn(); // swaps `worker` first so the old thread's exit is ignored
    old.terminate();
}

async function slice(stl, device, processDef) {
    await whenReady();
    return new Promise((resolve, reject) => {
        const id = ++seq;
        const done = (fn) => (v) => { clearTimeout(timer); pending = null; fn(v); };
        const timer = setTimeout(() => {
            pending = null;
            console.error(`slice ${id} exceeded ${SLICE_TIMEOUT_MS}ms; restarting engine`);
            restart();
            reject(Object.assign(new Error(`slice timed out after ${SLICE_TIMEOUT_MS / 1000}s`), { status: 504 }));
        }, SLICE_TIMEOUT_MS);
        pending = { id, resolve: done(resolve), reject: done(reject) };
        worker.postMessage({ id, stl, device, process: processDef }, [stl]);
    });
}

// worker-side state is thread-global: serialize requests
let queue = Promise.resolve();

const server = http.createServer((req, res) => {
    const send = (code, obj) => {
        if (res.headersSent) return;
        res.writeHead(code, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(obj));
    };
    if (req.method === 'GET' && req.url === '/health') {
        return send(200, { ok: true, ready: engineReady, busy });
    }
    if (req.method !== 'POST' || req.url !== '/slice') {
        return send(404, { ok: false, error: 'not found' });
    }
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
        size += c.length;
        if (size > MAX_BODY) {
            send(413, { ok: false, error: 'body too large' });
            req.destroy();
            return;
        }
        chunks.push(c);
    });
    req.on('end', () => {
        queue = queue.then(async () => {
            busy = true;
            try {
                const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
                const buf = Buffer.from(data.stl || '', 'base64');
                const stl = new Uint8Array(buf).buffer; // own ArrayBuffer -> transferable
                const { id: _id, ...result } = await slice(stl, data.device, data.process);
                if (result.ok) send(200, result);
                else send(500, { ok: false, error: result.error });
            } catch (e) {
                send(e.status || 500, { ok: false, error: String((e && e.message) || e) });
            } finally {
                busy = false;
            }
        }).catch(() => { });
    });
});
server.requestTimeout = 0; // complex parts can slice for minutes; SLICE_TIMEOUT_MS bounds it

spawn();
server.listen(PORT, () => console.log(`slicer sidecar listening on :${PORT} (slice timeout ${SLICE_TIMEOUT_MS}ms)`));
