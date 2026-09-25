"""Regression for Streamlit Cloud hot-reload import race handling."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "streamlit_app.py"


class FakeImportlib:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0
        self.invalidations = 0
        self.module = SimpleNamespace(marker="ok")

    def import_module(self, name):
        self.calls += 1
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        assert name == "src.etrade_data_cache"
        return self.module

    def invalidate_caches(self):
        self.invalidations += 1


class FakeTime:
    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def load_helper(fake_importlib, fake_time):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_import_etrade_data_cache"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"importlib": fake_importlib, "time": fake_time}
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace["_import_etrade_data_cache"]


def main():
    fake_importlib = FakeImportlib(
        [
            KeyError("src.etrade_data_cache"),
            KeyError("src.etrade_data_cache"),
        ]
    )
    fake_time = FakeTime()
    helper = load_helper(fake_importlib, fake_time)
    result = helper()
    assert result is fake_importlib.module
    assert fake_importlib.calls == 3
    assert fake_importlib.invalidations == 2
    assert fake_time.sleeps == [0.05, 0.1]

    wrong_error_importlib = FakeImportlib([KeyError("some.other.module")])
    helper = load_helper(wrong_error_importlib, FakeTime())
    try:
        helper()
    except KeyError as exc:
        assert exc.args == ("some.other.module",)
    else:
        raise AssertionError("Non-target KeyError must not be swallowed")

    print("Streamlit hot-reload E*TRADE cache import retry: PASS")


if __name__ == "__main__":
    main()
