"""Which loaded module IS the till.

Sixteen routers need the till module — for its store directory, its catalogue,
its price map. Each one carried its own copy of the answer:

    _TILL_NAMES = ("upload_app", "tools.upload_app")

Sixteen copies of a constant is sixteen places for it to be wrong, and it was.
`tools/upload_app.py` has a `__main__` block, so `python tools/upload_app.py`
is an obvious way to start it — and that registers the module under the name
`__main__`, which is in neither spelling. Every router that looked it up missed,
fell through to `import upload_app`, and got a SECOND COPY of a 6,000-line file
with its own dependency cache, its own store handle, and its own catalogue.

The symptom was not an error. The storefront served six products from the
second copy while the money service could price only the three the first one
held, so a customer built a basket, pressed PAY, and was told
`amber_in_basket` on a line the shop was openly advertising. Nothing anywhere
said the two halves of the shop were reading different files.

`storefront.py`'s own docstring had predicted this failure in detail. It was
still wrong, because the prediction lived next to one of the sixteen copies
instead of above all of them.

IDENTIFIED BY SHAPE, NOT BY NAME. `__main__` is whatever process happened to
start: under `make serve` it is uvicorn's CLI, under `pytest` it is pytest.
Trusting the name would hand a router the test runner. So a candidate is only
the till if it carries the till's own functions.
"""
from __future__ import annotations

from typing import Any

#: Every name the till can legitimately be registered under.
#:   upload_app        `uvicorn upload_app:app --app-dir tools`  (make serve)
#:   tools.upload_app  `from tools import upload_app`            (the suite)
#:   __main__          `python tools/upload_app.py`              (running it)
TILL_NAMES: tuple[str, ...] = ("upload_app", "tools.upload_app", "__main__")

#: Functions only the till has. Checked together: any one of them could be
#: coincidence in some other module, three of them is the till.
TILL_MARKS: tuple[str, ...] = ("store_dir", "priced_skus", "taught_skus")


def is_the_till(mod: Any) -> bool:
    """True when `mod` is the till module itself, whatever it is called."""
    return mod is not None and all(hasattr(mod, m) for m in TILL_MARKS)


def find_loaded_till(modules: dict[str, Any]) -> Any:
    """The till among already-imported modules, or None. Imports nothing.

    Pass `sys.modules`. Returning None is not an error — the caller decides
    whether to import a copy or refuse, and those callers differ.
    """
    for name in TILL_NAMES:
        mod = modules.get(name)
        if is_the_till(mod):
            return mod
    return None
