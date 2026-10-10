// Build-time patch for headless (single-process) operation.
// computeShadowStack routes every z through work.minions.queueAsync, but the
// minion pool cannot run in a single Node process (see worker.js concurrent=0
// patch). Without minions, queueAsync never resolves and automatic supports
// hang forever. When no minions are running, compute shadows inline instead —
// same call the minion would make: computeShadowAtZ(z, ztop, cachedStack).
const fs = require('fs');

const f = 'src/kiri/core/widget.js';
let s = fs.readFileSync(f, 'utf8');

const oldBlock = `        let pinc = 1 / zlist.length;
        let pval = 0;
        for (let z of zlist) {
            let p = work.minions.queueAsync({
                cmd: 'cam_shadow_z',
                z: z - 0.005,
                t: z + up
            }).then(reply => {
                shadow_stack[z - 0.005] = decode(reply.data);
                pval += pinc;
                progress(pval);
            });
            plist.push(p);
        }
        await Promise.all(plist);`;

const newBlock = `        let pinc = 1 / zlist.length;
        let pval = 0;
        if (!work.minions.running) {
            for (let z of zlist) {
                shadow_stack[z - 0.005] = this.computeShadowAtZ(z - 0.005, z + up, stack);
                pval += pinc;
                progress(pval);
            }
        } else {
            for (let z of zlist) {
                let p = work.minions.queueAsync({
                    cmd: 'cam_shadow_z',
                    z: z - 0.005,
                    t: z + up
                }).then(reply => {
                    shadow_stack[z - 0.005] = decode(reply.data);
                    pval += pinc;
                    progress(pval);
                });
                plist.push(p);
            }
            await Promise.all(plist);
        }`;

if (!s.includes(oldBlock)) {
    console.error('patch-shadow: anchor not found in widget.js — Kiri source changed, review this patch');
    process.exit(1);
}
fs.writeFileSync(f, s.replace(oldBlock, newBlock));
console.log('patch-shadow: headless inline shadow stack applied to widget.js');
