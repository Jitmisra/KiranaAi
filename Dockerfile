# GAWAAH — one image, two services.
#
# The counter is two processes on purpose (invariant 5): the TILL holds the
# catalogue, the camera and the books and NO gateway secret; the MONEY service
# is the only process that ever sees a Razorpay credential. They are the same
# code and the same models, so they are the same image — the COMMAND picks
# which one starts. Render runs one container per service and will start this
# image twice with two different commands, so nothing here may bake a service
# into an ENTRYPOINT.
#
#   the TILL   (catalogue + camera + books, serves ui/dist, no secrets)
#     python -m uvicorn upload_app:app --app-dir tools --host 0.0.0.0 --port $PORT
#     ^ this is the default CMD at the bottom of this file.
#
#   the MONEY  (the ONLY holder of gateway keys; a FACTORY, hence --factory)
#     python -m uvicorn --factory gawaah.live_app:app --host 0.0.0.0 --port $PORT
#
# NO SECRET IS BAKED IN. .env is excluded by .dockerignore and is never COPYed.
# Every credential arrives as a Render environment variable at run time, and
# RZP_MODE stays `sim` unless an operator deliberately changes it in the
# dashboard. GAWAAH_ALLOW_LIVE_KEYS is not set here, so gawaah/rzp_live.py keeps
# refusing key ids that do not start `rzp_test_`.


# ---------------------------------------------------------------------------
# Stage 1 — the front end.
#
# ui/dist is gitignored (.gitignore:12), so a fresh checkout has no built page
# and the image MUST build it. Node is a BUILD-time dependency only: nothing
# from this stage reaches the runtime image except the static bundle.
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS ui

# bash, so `pipefail` below is real. Under /bin/sh (dash) a failing build piped
# into `tee` would report the exit status of `tee` — i.e. success — and ship a
# truncated bundle.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /ui

# @playwright/test is a devDependency and `npm ci` installs devDependencies
# (vite and tsc live there too, so --omit=dev is not an option). Without this
# the install would download three browsers into an image that will never run
# a test.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    npm_config_fund=false \
    npm_config_audit=false

# Manifests first: the dependency layer is then cached across every source edit.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./

# `npm run build` is `tsc -b --noEmit && vite build`.
#
# A BUILD WARNING IS A BUILD FAILURE, exactly as the Makefile's `ui` target
# says. esbuild prints `Expected "}" to go with "{"` for a truncated stylesheet
# and then emits the bundle anyway, silently dropping the rest of that file.
# That shipped once. It must not ship out of a container either.
RUN npm run build 2>&1 | tee /tmp/ui-build.log \
 && if grep -q 'WARNING' /tmp/ui-build.log; then \
        echo ""; \
        echo "BUILD WARNING — treated as a failure. The bundle is not trustworthy:"; \
        grep -A2 'WARNING' /tmp/ui-build.log; \
        exit 1; \
    fi \
 && test -s dist/index.html


# ---------------------------------------------------------------------------
# Stage 2 — the runtime.
#
# python:3.14-slim-trixie. 3.14 matches the interpreter this code is developed
# and tested on (3.14.3 locally), and every pinned dependency has a linux
# wheel for it — measured, not assumed:
#   opencv-contrib-python-headless 5.0.0.93  cp37-abi3 manylinux_2_28  (abi3:
#                                            one wheel covers 3.7+, so 3.14 too)
#   numpy 2.5.2                              cp314 manylinux_2_28
#   pillow 12.3.0                            cp314 manylinux_2_28
#   zxing-cpp 3.1.1                          cp314 manylinux_2_28
#   pydantic-core 2.46.5                     cp314 manylinux
#
# `slim` (Debian trixie, glibc 2.41) rather than `alpine`: opencv publishes no
# musllinux wheel at all, so alpine would mean compiling OpenCV from source.
# trixie's glibc clears the manylinux_2_28 floor those wheels require.
#
# SYSTEM PACKAGES ARE ONE LINE LONG, and each half of it was measured rather
# than assumed:
#   - NO GL, NO GTK, NO libglib. The headless OpenCV build exists precisely so
#     none of that is needed, and `import cv2, numpy, PIL, zxingcpp` was
#     verified to succeed in a bare python:3.14-slim-trixie with zero extra
#     packages — cv2.aruco, cv2.barcode and cv2.dnn.readNetFromONNX on both
#     models included.
#   - NO ca-certificates line. requirements.txt is right that TLS to
#     api.razorpay.com trusts the system store because nothing ships certifi —
#     but python:slim already installs ca-certificates, which was confirmed in
#     the base image: ssl.create_default_context() loads 150 CAs and an https
#     request to api.razorpay.com returns 200. Adding it would be a no-op.
#   - NO curl. The health check below is written in Python instead.
#   - fonts-dejavu-core IS needed, and this is the evidence. Pillow is in
#     requirements.txt for tools/seed_shop.py, which is how a fresh deploy gets
#     a shop at all (results/ is gitignored). tools/packshot.py opens the first
#     font that exists from a fixed list of paths; in the bare base image every
#     one of them is missing, `_font(...)` returns None, and the seeder draws
#     product tiles with no product name on them. This package puts
#     DejaVuSans{,-Bold}.ttf at exactly the /usr/share/fonts/truetype/dejavu/
#     paths that list names. Devanagari is deliberately NOT added with it:
#     packshot drops the Hindi line unless Pillow was built with Raqm shaping,
#     which this wheel was not, so a Noto face would add megabytes and change
#     nothing.
# ---------------------------------------------------------------------------
FROM python:3.14-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH=/app

WORKDIR /app

# See the note above for why this list is one package and not five.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Dependencies before source: editing a route must not re-download OpenCV.
# requirements.txt is the runtime list only — what gawaah/ and tools/ actually
# import. pytest, playwright and the rest of the development venv are not in it
# and must not be.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The recogniser's weights. models/ IS tracked, unlike results/ and ui/dist.
# SqueezeNet is not optional — recognition needs it. YOLO is, and its absence
# is a supported state.
COPY models/ ./models/

# The two services.
COPY gawaah/ ./gawaah/
COPY tools/ ./tools/
# The two start scripts. See docker/till.sh for why the start command is a file.
COPY docker/ ./docker/

# The built page. tools/upload_app.py resolves UI_DIST as
# <parent of tools>/ui/dist, so /app/ui/dist is exactly where it looks.
COPY --from=ui /ui/dist/ ./ui/dist/

# Non-root. `results/` is the shop's own runtime state — the catalogue, taught
# products, the ledger — and it is gitignored, so a fresh image does not have
# one. It is created here and handed to the runtime user so the counter can
# write on a read-only-by-default root filesystem. On Render, point
# GAWAAH_SHOP_DIR / GAWAAH_DATA_DIR at a mounted disk to make it outlive a
# deploy; without a disk this is ephemeral, and `tools/seed_shop.py` rebuilds a
# whole shop from nothing.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin gawaah \
 && mkdir -p /app/results/shop \
 && chown -R gawaah:gawaah /app/results
USER gawaah

# Render injects $PORT and expects the process to bind it on 0.0.0.0. The
# default is the till's local port so `docker run -p 8790:8790 <image>` behaves
# like `make serve`; the money service is started with `-e PORT=8788` and its
# own command.
ENV PORT=8790
# Documentation only — Render publishes whatever the process binds. These are
# the two local ports, so that `docker run -P` mirrors `make serve` and
# `make serve-money`.
EXPOSE 8790 8788

# Both services answer /health, and on the till it is one of the two paths the
# auth guard leaves open by definition. Written in Python so no curl is needed;
# it reads $PORT so the same check is correct for either service.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8790')+'/health', timeout=4).status == 200 else 1)"

# CMD, not ENTRYPOINT: Render's per-service command replaces this wholesale,
# which is how one image runs two different processes. See the header for the
# money service's command.
#
# No --proxy-headers / --forwarded-allow-ips, on purpose. Render terminates TLS
# in front of the container, so those flags matter only if the app reads the
# client address or builds absolute URLs from the request. It does neither:
# there is no `request.client`, no `request.base_url` and no `url_for` anywhere
# in gawaah/ or tools/ (`parchi.py`'s `base_url()` is the language model's
# endpoint, not this server's). Adding the flags would mean trusting an
# X-Forwarded-For from any peer to change nothing.
CMD ["sh", "-c", "exec python -m uvicorn upload_app:app --app-dir tools --host 0.0.0.0 --port ${PORT:-8790}"]
