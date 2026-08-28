#!/usr/bin/env python3
"""INVARIANT 1 enforcement: no float in the money path.

Walks the AST of every module in the money path and fails on:
  - float literals
  - float() casts
  - true division (/) on anything, since it silently produces a float
Exit 1 on any finding. Wired into `make test` and CI.
"""
import ast, sys, pathlib

MONEY_PATH = ["gawaah/money.py", "gawaah/ledger.py", "gawaah/paisa.py",
              "gawaah/kernel.py", "gawaah/session.py"]

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
    if findings:
        print(f"\nFAIL: {findings} float violation(s) in the money path")
        return 1
    print(f"no-float lint: PASS ({checked} money-path modules clean)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
