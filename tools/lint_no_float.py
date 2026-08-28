#!/usr/bin/env python3
"""INVARIANT 1 enforcement: no float in the money path.

Walks the AST of every module in the money path and fails on:
  - float literals
  - float() casts
  - true division (/) on anything, since it silently produces a float
Exit 1 on any finding. Wired into `make test` and CI.
"""
import ast, sys, pathlib

# Whole-file strict: these modules are money end to end, so ANY float is a bug.
# webhook.py added after the D-day audit -- it handles amount_paise and was
# silently outside the lint, so INVARIANT 1 was unenforced there. It passes clean.
MONEY_PATH = ["gawaah/money.py", "gawaah/ledger.py", "gawaah/paisa.py",
              "gawaah/kernel.py", "gawaah/session.py", "gawaah/webhook.py"]

# Repo-wide semantic check. Mixed modules (chilla.py does screen GEOMETRY as well
# as ledger matching) legitimately use floats for mm, angles, probabilities and
# durations. Blanket-linting them yields ~80 false positives, which trains people
# to ignore the lint -- worse than no lint. So instead we check what actually
# matters: a float must never flow into an identifier that names MONEY.
MONEY_NAMES = ("paise", "amount", "price", "total", "rupee", "mrp", "balance")

# A name carrying an explicit NON-money unit is dimensional, not monetary.
# HERO_AMOUNT_CAP_MM is the cap height of the amount text on a phone screen in
# millimetres -- it matches "amount" but it is geometry, and floats are correct
# there. Without this, the semantic lint cries wolf on real measurements.
UNIT_SUFFIXES = ("_mm", "_mm2", "_px", "_deg", "_s", "_ms", "_ns", "_us",
                 "_ratio", "_frac", "_fraction", "_pct", "_hz", "_lux")

class V(ast.NodeVisitor):
    def __init__(self, f): self.f, self.bad = f, []
    def visit_Constant(self, n):
        if isinstance(n.value, float):
            self.bad.append((n.lineno, f"float literal {n.value!r}"))
        self.generic_visit(n)
    def visit_Call(self, n):
        if isinstance(n.func, ast.Name) and n.func.id == "float":
            self.bad.append((n.lineno, "float() cast"))
        self.generic_visit(n)
    def visit_BinOp(self, n):
        if isinstance(n.op, ast.Div):
            self.bad.append((n.lineno, "true division '/' produces a float — use //"))
        self.generic_visit(n)

class SemanticV(ast.NodeVisitor):
    """Flag floats reaching money-named identifiers, anywhere in the package."""

    def __init__(self, f):
        self.f, self.bad = f, []

    @staticmethod
    def _is_money_name(name: str) -> bool:
        n = name.lower()
        if any(n.endswith(u) for u in UNIT_SUFFIXES):
            return False
        return any(k in n for k in MONEY_NAMES)

    @staticmethod
    def _floatish(node):
        for x in ast.walk(node):
            if isinstance(x, ast.Constant) and isinstance(x.value, float):
                return "float literal"
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "float":
                return "float() cast"
            if isinstance(x, ast.BinOp) and isinstance(x.op, ast.Div):
                return "true division"
        return None

    def visit_AnnAssign(self, n):
        if isinstance(n.target, ast.Name) and self._is_money_name(n.target.id):
            if isinstance(n.annotation, ast.Name) and n.annotation.id == "float":
                self.bad.append((n.lineno, f"money-named '{n.target.id}' annotated float"))
        self.generic_visit(n)

    def visit_Assign(self, n):
        names = [t.id for t in n.targets if isinstance(t, ast.Name)]
        names += [t.attr for t in n.targets if isinstance(t, ast.Attribute)]
        if any(self._is_money_name(x) for x in names) and n.value is not None:
            why = self._floatish(n.value)
            if why:
                self.bad.append((n.lineno, f"{why} assigned to money-named {names}"))
        self.generic_visit(n)

    def visit_arg(self, n):
        if self._is_money_name(n.arg) and isinstance(n.annotation, ast.Name):
            if n.annotation.id == "float":
                self.bad.append((n.lineno, f"money-named arg '{n.arg}' annotated float"))
        self.generic_visit(n)


def semantic_scan(root):
    findings = 0
    files = 0
    for p in sorted((root / "gawaah").glob("*.py")) + sorted((root / "tools").glob("*.py")):
        files += 1
        v = SemanticV(p.relative_to(root))
        v.visit(ast.parse(p.read_text()))
        for line, why in v.bad:
            print(f"  {v.f}:{line}  {why}")
            findings += 1
    return findings, files


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    findings = 0
    checked = 0
    for rel in MONEY_PATH:
        p = root / rel
        if not p.exists():
            continue
        checked += 1
        v = V(rel); v.visit(ast.parse(p.read_text()))
        for line, why in v.bad:
            print(f"  {rel}:{line}  {why}")
            findings += 1
    sem, scanned = semantic_scan(root)
    if findings or sem:
        print(f"\nFAIL: {findings} strict + {sem} semantic float violation(s)")
        return 1
    print(f"no-float lint: PASS ({checked} strict modules, "
          f"{scanned} files semantically scanned for floats reaching money)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
