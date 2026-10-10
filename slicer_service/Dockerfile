FROM node:22-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /grid

# Pinned grid-apps commit (Kiri:Moto engine v4.7.0)
RUN git init . \
    && git remote add origin https://github.com/gridspace/grid-apps.git \
    && git fetch --depth 1 origin d138275bbe9d4e4030d8cb8b2dcf66607da9295a \
    && git checkout FETCH_HEAD

# ponytail: force inline polygon ops — the minion worker pool cannot run in a
# single Node process (headless fake-Worker round-trip drops queued replies).
# Single-core slicing is fine for on-demand estimates; upgrade path is a real
# worker_threads pool if estimate latency ever matters.
RUN sed -i 's/Math\.max(4, self\.Worker/Math.max(0, self.Worker/' src/kiri/run/worker.js \
    && grep -q "Math.max(0, self.Worker" src/kiri/run/worker.js

# computeShadowStack (automatic supports) has no inline fallback and would hang
# with the pool disabled — patch it to run shadows in-process when idle.
COPY patch-shadow.js /tmp/patch-shadow.js
RUN node /tmp/patch-shadow.js \
    && grep -q "work.minions.running" src/kiri/core/widget.js

ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
# grid-apps ships no lockfile; ours is the tree the sidecar was validated with
COPY package-lock.json /grid/package-lock.json
RUN npm ci --no-audit --no-fund \
    && npm run webpack-ext \
    && node bin/esbuild.config.mjs dev

# emscripten resolves manifold.wasm as ../wasm/ relative to CWD
WORKDIR /grid/src/pack

COPY server.js engine-worker.js /

EXPOSE 8090
CMD ["node", "/server.js"]
