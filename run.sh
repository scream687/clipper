#!/bin/sh
# Start the clipper at http://127.0.0.1:8765 (this machine only).
cd "$(dirname "$0")" || exit 1
# Lets Pillow find Homebrew's fribidi, which it needs to shape Hindi hook text.
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
exec uv run uvicorn app.server:app --host 127.0.0.1 --port "${CLIPPER_PORT:-8765}"
