#!/bin/sh
# THE TILL, as Render starts it.
#
# This lives in a FILE, not in render.yaml's `dockerCommand`, because a
# multi-statement shell command does not survive the trip. Render re-quotes the
# field before handing it to the container, and a command that began
# `sh -c 'mkdir -p ...; exec python ...'` arrived at the shell as ONE word:
#
#   sh: 1: mkdir -p "$GAWAAH_DATA_DIR" ... --port "$PORT": not found
#   ==> Exited with status 127
#
# A path with no quotes, no semicolons and no $ in it cannot be mangled that
# way, so `dockerCommand` is now just `sh docker/till.sh` and everything that
# needs a shell is in here, where it is also readable and testable.
set -e

# Fail LOUDLY at boot rather than deep inside the first write. The image
# creates and chowns /app/results for uid 10001; if these are ever pointed
# somewhere the runtime user cannot write, this is where it should stop.
mkdir -p "${GAWAAH_DATA_DIR:-/app/results}" "${GAWAAH_SHOP_DIR:-/app/results/shop}"

# A fresh container has no shop: results/ is gitignored, so the catalogue does
# not exist until something seeds it. The seeder talks to this very server over
# HTTP, so it has to run AFTER the server is listening -- hence the background
# loop that retries while the port is still closed. Exit code 1 means "not up
# yet"; anything else means it finished or failed for a reason retrying cannot
# fix, and the loop stops either way.
if [ "$GAWAAH_SEED_ON_BOOT" = "1" ]; then
  (
    i=0
    while [ "$i" -lt 24 ]; do
      sleep 5
      python tools/seed_shop.py --till "http://127.0.0.1:$PORT" && break
      [ $? -eq 1 ] || break
      i=$((i + 1))
    done
  ) &
fi

# exec, so uvicorn is PID 1 and Render's stop signal reaches it rather than
# this script.
exec python -m uvicorn upload_app:app --app-dir tools \
     --host 0.0.0.0 --port "${PORT:-8790}"
