#!/usr/bin/env python3
"""Static architecture guard for Raj's Terminal.

Run before/after feature changes:

    python scripts/validate_architecture.py

The validator is intentionally dependency-free. It catches the failure modes
that have caused unrelated tabs to break in the past:
- production route files missing or syntactically invalid;
- Risk Sizing compatibility route no longer pointing to v10;
- top-level feature imports disappearing from streamlit_app.py;
- module-level monkey-patching of Streamlit functions in production feature
  modules (for example `st.caption = ...` at import time);
- module-level CSS constants written as Python f-strings, where ordinary CSS
  braces can be interpreted as Python expressions and crash app startup.

This does not replace UI testing. It is a fast boundary check before deploy.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


PRODUCTION_PYTHON_FILES = [
    ROOT / "streamlit_app.py",
    SRC / "theme.py",
    SRC / "layout_guardrails.py",
    SRC / "risk_sizing_ui_v7.py",
    SRC / "risk_sizing_ui_v10.py",
    SRC / "schwab_risk_sizing_ui.py",
    SRC / "gex_workspace_v2.py",
    SRC / "gex_ui_v3.py",
    SRC / "gex_ui_v3_base.py",
    SRC / "etrade_connection_ui_v2.py",
    SRC / "holdings_snapshot_mode.py",
    SRC / "tab_bar_v4.py",
    SRC / "bull_debit_ui.py",
]


REQUIRED_APP_IMPORTS = [
    "from src.etrade_connection_ui_v2 import render_compact_etrade_connection",
    "from src.gex_workspace_v2 import render_gex as render_gex_workspace",
    "from src.holdings_snapshot_mode import build_manual_holdings_renderer",
    "from src.risk_sizing_ui_v7 import render_risk_sizing",
    "from src.schwab_risk_sizing_ui import render_schwab_risk_sizing",
    "from src.tab_bar_v4 import render_terminal_tab_bar",
]


# Streamlit function reassignment at module import time can affect every tab.
# Local variables such as `previous_button = st.button` are fine. The guard
# only rejects assignment TO a Streamlit attribute at module scope.
FORBIDDEN_STREAMLIT_ASSIGNMENTS = {
    "button",
    "caption",
    "columns",
    "dataframe",
    "empty",
    "markdown",
    "number_input",
    "radio",
    "selectbox",
    "subheader",
    "text_input",
    "warning",
}


class ModuleScopeStreamlitAssignmentVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.depth = 0
        self.violations: list[tuple[int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def _check_target(self, target: ast.expr, lineno: int) -> None:
        if self.depth != 0:
            return
        if not isinstance(target, ast.Attribute):
            return
        if not isinstance(target.value, ast.Name) or target.value.id != "st":
            return
        if target.attr in FORBIDDEN_STREAMLIT_ASSIGNMENTS:
            self.violations.append((lineno, target.attr))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check_target(target, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_target(node.target, node.lineno)
        self.generic_visit(node)


class ModuleScopeCssFStringVisitor(ast.NodeVisitor):
    """Reject module-level *_CSS constants implemented as f-strings.

    A CSS block normally contains many literal ``{ ... }`` braces. A Python
    f-string can interpret those braces as Python expressions while importing a
    shared module, producing runtime NameError/ValueError failures before the
    Streamlit app renders. Prefer a plain string plus explicit ``.replace``.
    """

    def __init__(self) -> None:
        self.depth = 0
        self.violations: list[tuple[int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def _check(self, target: ast.expr, value: ast.expr | None, lineno: int) -> None:
        if self.depth != 0 or not isinstance(target, ast.Name):
            return
        if not target.id.upper().endswith("_CSS"):
            return
        if isinstance(value, ast.JoinedStr):
            self.violations.append((lineno, target.id))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check(target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check(node.target, node.value, node.lineno)
        self.generic_visit(node)



def parse_file(path: Path) -> ast.Module:
    text = path.read_text(encoding="utf-8")
    return ast.parse(text, filename=str(path))



def main() -> int:
    errors: list[str] = []

    for path in PRODUCTION_PYTHON_FILES:
        if not path.exists():
            errors.append(f"MISSING production file: {path.relative_to(ROOT)}")
            continue
        try:
            tree = parse_file(path)
        except SyntaxError as exc:
            errors.append(
                f"SYNTAX ERROR {path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}"
            )
            continue

        streamlit_visitor = ModuleScopeStreamlitAssignmentVisitor()
        streamlit_visitor.visit(tree)
        for lineno, attribute in streamlit_visitor.violations:
            errors.append(
                f"GLOBAL STREAMLIT PATCH {path.relative_to(ROOT)}:{lineno}: "
                f"st.{attribute} = ... is forbidden at module scope"
            )

        css_visitor = ModuleScopeCssFStringVisitor()
        css_visitor.visit(tree)
        for lineno, constant_name in css_visitor.violations:
            errors.append(
                f"UNSAFE CSS F-STRING {path.relative_to(ROOT)}:{lineno}: "
                f"{constant_name} must be a plain string; use .replace() for values"
            )

    app_path = ROOT / "streamlit_app.py"
    if app_path.exists():
        app_text = app_path.read_text(encoding="utf-8")
        for required in REQUIRED_APP_IMPORTS:
            if required not in app_text:
                errors.append(f"APP ROUTE MISSING: {required}")
        if 'elif active_tab == "SCHWAB RISK SIZING":' not in app_text:
            errors.append("APP ROUTE MISSING: SCHWAB RISK SIZING dispatch")

    nav_path = SRC / "tab_bar_v4.py"
    if nav_path.exists():
        nav_text = nav_path.read_text(encoding="utf-8")
        if '"SCHWAB RISK SIZING"' not in nav_text:
            errors.append("NAV ROUTE MISSING: SCHWAB RISK SIZING tab")
        if '"RISK SIZING": "E*TRADE RISK SIZING"' not in nav_text:
            errors.append("NAV LABEL MISSING: E*TRADE RISK SIZING display label")

    risk_route = SRC / "risk_sizing_ui_v7.py"
    if risk_route.exists():
        risk_text = risk_route.read_text(encoding="utf-8")
        expected = "from src.risk_sizing_ui_v10 import render_risk_sizing"
        if expected not in risk_text:
            errors.append(
                "RISK ROUTE CHANGED: src/risk_sizing_ui_v7.py must route production "
                "Risk Sizing to src/risk_sizing_ui_v10.py"
            )

    architecture = SRC / "ARCHITECTURE.md"
    manifest = SRC / "production_manifest.py"
    if not architecture.exists():
        errors.append("MISSING src/ARCHITECTURE.md")
    if not manifest.exists():
        errors.append("MISSING src/production_manifest.py")

    if errors:
        print("ARCHITECTURE GUARD: FAILED")
        for error in errors:
            print(f" - {error}")
        return 1

    print("ARCHITECTURE GUARD: PASS")
    print(
        "Production routes parse successfully, contain no forbidden module-level "
        "Streamlit monkey patches, and contain no unsafe module-level CSS f-strings."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
