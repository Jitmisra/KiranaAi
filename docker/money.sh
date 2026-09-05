#!/bin/sh
# THE MONEY SERVICE, as Render starts it. See docker/till.sh for why this is a
# file and not an inline `dockerCommand`.
#
# `--factory` because gawaah.live_app:app is a callable that BUILDS the app, not
# the app itself. uvicorn detects it either way on 0.52, but naming it means a
# future uvicorn that stops guessing does not take this service down quietly.
set -e
mkdir -p "${GAWAAH_DATA_DIR:-/app/results}" "${GAWAAH_SHOP_DIR:-/app/results/shop}"
exec python -m uvicorn --factory gawaah.live_app:app \
     --host 0.0.0.0 --port "${PORT:-8788}"
