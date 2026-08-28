VENV := ./.venv/bin
.PHONY: test lint verify-ledger bench clean

test: lint
	$(VENV)/python -m pytest tests/ -q

lint:
	$(VENV)/python tools/lint_no_float.py

verify-ledger:
	$(VENV)/python -c "import sys;from pathlib import Path;from gawaah.ledger import verify;\
ok,n,h,e=verify(Path('results/audit.jsonl'));print(f'lines={n} head={h[:16]}... ok={ok}');\
sys.exit(0 if ok else 1)"

bench:
	@echo "not implemented yet - S7"

clean:
	rm -rf .pytest_cache **/__pycache__
