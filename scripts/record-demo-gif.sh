#!/usr/bin/env bash
# Records docs/img/demo.gif from the live app (needs `make up && make ingest-demo`, a real LLM key in .env,
# and ffmpeg). Playwright records one WebM per browser context (Alice, then Bob); they are joined and
# converted to a palette-optimised GIF.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf frontend/demo/.recording
(cd frontend && pnpm exec playwright test -c demo/playwright.demo.config.ts)
# Oldest first: the Alice context closes before Bob's starts. (Plain loop: macOS ships bash 3.2, no mapfile.)
LIST=$(mktemp)
for v in $(ls -tr frontend/demo/.recording/*/*.webm 2>/dev/null); do echo "file '$PWD/$v'" >> "$LIST"; done
[ -s "$LIST" ] || { echo "no video recorded" >&2; exit 1; }
ffmpeg -y -loglevel error -f concat -safe 0 -i "$LIST" -c:v libvpx-vp9 -b:v 0 -crf 32 /tmp/rag-demo.webm
ffmpeg -y -loglevel error -i /tmp/rag-demo.webm \
  -vf "fps=10,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" \
  docs/img/demo.gif
rm -f "$LIST"
echo "wrote docs/img/demo.gif ($(du -h docs/img/demo.gif | cut -f1))"
