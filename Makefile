VENV := ./.venv/bin
FEED := /tmp/gawaah_cam.y4m
.PHONY: test lint verify-ledger clean ui ui-dev ui-test e2e fakecam serve serve-money all-tests preflight

# ---------------------------------------------------------------- the suite --

test: lint
	$(VENV)/python -m pytest tests/ -q
	cd ui && npm run test

lint:
	$(VENV)/python tools/lint_no_float.py

# Everything, including the browser. Needs the till running on :8790.
all-tests: test e2e

# Before every demo. Exercises the path a rupee travels instead of asking
# whether a process is running -- a running `cloudflared` whose tunnel has been
# revoked looks identical to a healthy one, and that cost a real payment once.
# GAWAAH_TUNNEL_DIR points at wherever the tunnel hostnames were written.
preflight:
	$(VENV)/python tools/preflight.py

# ------------------------------------------------------------- the frontend --

# REQUIRED, not optional. There is no second front end any more: the server used
# to carry a 2,500-line inline-script copy of this page and fall back to it, and
# the two copies disagreed. `/` now returns 503 with an instruction when this
# has not been run, which is a better failure than a different product.
# A BUILD WARNING IS A BUILD FAILURE HERE. esbuild prints
# `Expected "}" to go with "{"` for a truncated stylesheet and then emits the
# bundle anyway, silently dropping the rest of that file. That shipped once: a
# regex ate one closing brace, the build "succeeded", and an end-to-end test
# failed twenty lines later on a drag whose CSS no longer existed. The warning
# was on screen the whole time. Now it stops the build.
ui:
	cd ui && npm install --no-audit --no-fund
	cd ui && npm run build 2>&1 | tee /tmp/gawaah_build.log
	@if grep -q 'WARNING' /tmp/gawaah_build.log; then \
		echo ""; echo "BUILD WARNING — treated as a failure. The bundle above is not trustworthy:"; \
		grep -A2 'WARNING' /tmp/gawaah_build.log; exit 1; fi

ui-dev:
	cd ui && npm run dev

ui-test:
	cd ui && npm run test

# --------------------------------------------------------------- end to end --

# A fake camera feed: a QR held in the TOP-LEFT CORNER, rolled 37 degrees. A
# till that crops to the centre of the view reads nothing here, which is the
# whole point of testing against it.
fakecam:
	$(VENV)/python tools/make_fake_cam.py $(FEED)

# Real Chromium, real getUserMedia, real server. Start both first:
#   make serve        the till, :8790     (in another shell)
#   make serve-money  the keys,  :8788    (in another shell)
e2e: fakecam
	cd ui && npx playwright install chromium --with-deps 2>/dev/null || true
	cd ui && GAWAAH_FEED=$(FEED) npx playwright test

# ------------------------------------------------------------- the services --

# The till. Holds the catalogue and the camera endpoints. No secrets.
# The till gets EXACTLY ONE variable out of .env, by name.
#
# `serve-money` sources the whole file because the money service is the sole
# holder of the gateway keys. The till must never see them -- that separation is
# invariant 5, and `set -a; . ./.env` here would quietly break it.
#
# But the assistant runs inside the till and reads XAI_API_KEY from its
# environment, so without this line a shopkeeper can add the key to .env,
# restart, and watch nothing change: the router stays on the local parser and
# says so in a field nobody reads. One grep, one variable, no secrets.
# THE TILL GETS THE MODEL KEYS AND NOTHING ELSE. It deliberately does NOT
# `. ./.env` the way serve-money does: invariant 5 says the money service is the
# only process that holds gateway credentials, and sourcing the whole file would
# hand RAZORPAY_KEY_SECRET to the process that must never have it.
#
# It used to export only XAI_API_KEY. That name left .env when this counter
# moved to Gemini, so the grep found nothing, the till started with no key at
# all, and the advisor answered every question with "no model is set, so I can
# only read the figures" — while a working GOOGLE_API_KEY sat in .env the whole
# time. The same key is what gives the advisor a natural voice, so both the
# reasoning and the speech were off for the same reason.
serve:
	@set -a; \
	for v in GOOGLE_API_KEY GAWAAH_LLM_BASE_URL GAWAAH_LLM_MODEL GAWAAH_TTS_MODEL GAWAAH_TTS_VOICE XAI_API_KEY GAWAAH_LLM_KEY; do \
		line="$$(grep -E "^$$v=" .env 2>/dev/null | head -1 | cut -d= -f2-)"; \
		[ -n "$$line" ] && export $$v="$$line"; \
	done; set +a; \
	if [ -n "$$GOOGLE_API_KEY$$XAI_API_KEY$$GAWAAH_LLM_KEY" ]; then \
		echo "advisor: a model key is set -- it can reason, and it has a voice"; \
	else echo "advisor: no model key -- the deterministic parser answers, browser voice only"; fi; \
	$(VENV)/python -m uvicorn upload_app:app --app-dir tools --host 127.0.0.1 --port 8790

# The money. THE ONLY PROCESS WITH GATEWAY CREDENTIALS — invariant 5. It
# re-prices every witness from its own tables before it mints, so the till
# proposes and never decides. RZP_MODE=live uses the real gateway with test
# keys; the default in .env is the simulator.
serve-money:
	set -a; . ./.env; set +a; \
	$(VENV)/uvicorn --factory gawaah.live_app:app --host 127.0.0.1 --port 8788

# ------------------------------------------------------------------- other --

verify-ledger:
	$(VENV)/python -c "import sys;from pathlib import Path;from gawaah.ledger import verify;\
ok,n,h,e=verify(Path('results/audit.jsonl'));print(f'lines={n} head={h[:16]}... ok={ok}');\
sys.exit(0 if ok else 1)"

clean:
	rm -rf .pytest_cache .hypothesis .coverage **/__pycache__ ui/dist ui/test-results
