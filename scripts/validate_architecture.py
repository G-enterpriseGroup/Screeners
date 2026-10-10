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
    SRC / "heatmaps_ui.py",
    SRC / "gex_ui.py",
    SRC / "gex_cboe.py",
    SRC / "gex_github_bridge.py",
    SRC / "gex_ui_v3.py",
    SRC / "gex_ui_v3_base.py",
    SRC / "etrade_connection_ui_v2.py",
    SRC / "etrade_client.py",
    SRC / "option_book.py",
    SRC / "option_book_ui.py",
    SRC / "holdings_snapshot_mode.py",
    SRC / "performance_ui.py",
    SRC / "protective_puts.py",
    SRC / "protective_puts_sources.py",
    SRC / "protective_puts_ui.py",
    SRC / "yfinance_options.py",
    SRC / "rebalance_portfolio_ui.py",
    SRC / "tab_bar_v4.py",
    SRC / "bull_debit_ui.py",
]


REQUIRED_APP_IMPORTS = [
    "from src.etrade_connection_ui_v2 import render_compact_etrade_connection",
    "from src.gex_workspace_v2 import render_gex as render_gex_workspace",
    "from src.heatmaps_ui import render_heatmaps",
    "from src.holdings_snapshot_mode import build_manual_holdings_renderer",
    "from src.option_book_ui import render_option_book",
    "from src.performance_ui import render_performance",
    "from src.protective_puts_ui import render_protective_puts",
    "from src.rebalance_portfolio_ui import render_rebalance_portfolio",
    "from src.risk_sizing_ui_v7 import render_risk_sizing",
    "from src.risk_sizing_ui_v10 import maybe_auto_watch_risk_entries",
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
        if 'elif active_tab == "OPTION BOOK":' not in app_text:
            errors.append("APP ROUTE MISSING: OPTION BOOK dispatch")
        if 'elif active_tab == "PERFORMANCE":' not in app_text:
            errors.append("APP ROUTE MISSING: PERFORMANCE dispatch")
        if 'elif active_tab == "PROTECTIVE PUTS":' not in app_text:
            errors.append("APP ROUTE MISSING: PROTECTIVE PUTS dispatch")
        if 'elif active_tab == "REBALANCE PORTFOLIO":' not in app_text:
            errors.append("APP ROUTE MISSING: REBALANCE PORTFOLIO dispatch")
        if "maybe_auto_watch_risk_entries(_live_etrade_client())" not in app_text:
            errors.append("RISK AUTO-WATCH HOOK MISSING from terminal_background_hooks")

    nav_path = SRC / "tab_bar_v4.py"
    if nav_path.exists():
        nav_text = nav_path.read_text(encoding="utf-8")
        if '"SCHWAB RISK SIZING"' not in nav_text:
            errors.append("NAV ROUTE MISSING: SCHWAB RISK SIZING tab")
        if '"RISK SIZING": "E*TRADE RISK SIZING"' not in nav_text:
            errors.append("NAV LABEL MISSING: E*TRADE RISK SIZING display label")
        if '"OPTION BOOK"' not in nav_text:
            errors.append("NAV ROUTE MISSING: OPTION BOOK tab")
        if '"PERFORMANCE"' not in nav_text:
            errors.append("NAV ROUTE MISSING: PERFORMANCE tab")
        if '"PROTECTIVE PUTS"' not in nav_text:
            errors.append("NAV ROUTE MISSING: PROTECTIVE PUTS tab")

    risk_route = SRC / "risk_sizing_ui_v7.py"
    if risk_route.exists():
        risk_text = risk_route.read_text(encoding="utf-8")
        expected = "from src.risk_sizing_ui_v10 import render_risk_sizing"
        if expected not in risk_text:
            errors.append(
                "RISK ROUTE CHANGED: src/risk_sizing_ui_v7.py must route production "
                "Risk Sizing to src/risk_sizing_ui_v10.py"
            )

    option_book = SRC / "option_book.py"
    option_book_ui = SRC / "option_book_ui.py"
    etrade_client = SRC / "etrade_client.py"
    if option_book.exists() and '"PlaceOrderRequest":' in option_book.read_text(encoding="utf-8"):
        errors.append("OPTION BOOK SAFETY: helper must not construct a PlaceOrderRequest payload")
    if option_book_ui.exists() and "place_order(" in option_book_ui.read_text(encoding="utf-8"):
        errors.append("OPTION BOOK SAFETY: UI must not call live place_order")
    if option_book_ui.exists() and "cancel_order(" in option_book_ui.read_text(encoding="utf-8"):
        errors.append("OPTION BOOK SAFETY: UI must not call live cancel_order")
    if etrade_client.exists():
        client_text = etrade_client.read_text(encoding="utf-8")
        if "def preview_order(" not in client_text:
            errors.append("OPTION BOOK ROUTE MISSING: ETradeClient.preview_order")
        if "def place_order(" not in client_text:
            errors.append("RISK LIVE ORDER ROUTE MISSING: ETradeClient.place_order")
        if "def list_orders(" not in client_text:
            errors.append("RISK LIVE ORDER ROUTE MISSING: ETradeClient.list_orders")
        if "def cancel_order(" not in client_text:
            errors.append("RISK LIVE ORDER ROUTE MISSING: ETradeClient.cancel_order")
        if "def get_transactions(" not in client_text:
            errors.append("PERFORMANCE DATA ROUTE MISSING: ETradeClient.get_transactions")
        if '"CancelOrderRequest": {"orderId": order_number}' not in client_text:
            errors.append("RISK LIVE ORDER ROUTE MISSING: ETradeClient CancelOrderRequest payload")

    yfinance_options = SRC / "yfinance_options.py"
    if yfinance_options.exists():
        fallback_text = yfinance_options.read_text(encoding="utf-8")
        for required in (
            "class YFinanceOptionsClient",
            "def options_market_client(",
            "def get_option_expirations(",
            "def get_option_chain(",
            "is_yfinance_options_fallback = True",
        ):
            if required not in fallback_text:
                errors.append("OPTIONS FALLBACK ROUTE MISSING: " + required)
    for owner_name in ("vertical_options_ui.py", "option_book_ui.py", "bull_debit_ui.py", "gex_workspace_v2.py"):
        owner = SRC / owner_name
        if owner.exists() and "options_market_client" not in owner.read_text(encoding="utf-8"):
            errors.append(f"OPTIONS FALLBACK NOT WIRED: {owner_name}")

    protective_ui = SRC / "protective_puts_ui.py"
    if protective_ui.exists():
        protective_text = protective_ui.read_text(encoding="utf-8")
        for forbidden in ("preview_order(", "place_order(", "cancel_order("):
            if forbidden in protective_text:
                errors.append("PROTECTIVE PUTS SAFETY: read-only analytics may not call " + forbidden)

    performance_ui = SRC / "performance_ui.py"
    if performance_ui.exists():
        performance_text = performance_ui.read_text(encoding="utf-8")
        for forbidden in ("preview_order(", "place_order(", "cancel_order("):
            if forbidden in performance_text:
                errors.append("PERFORMANCE SAFETY: read-only Performance may not call " + forbidden)
        for required in (
            '@st.fragment(run_every=_LIVE_REFRESH_SECONDS)',
            'columns=["PERFORMANCE", "TODAY", "MTD", "YTD", "ALL-TIME"]',
            'missing history is never invented',
        ):
            if required not in performance_text:
                errors.append("PERFORMANCE ROUTE MISSING: " + required)

    rebalance_ui = SRC / "rebalance_portfolio_ui.py"
    if rebalance_ui.exists():
        rebalance_text = rebalance_ui.read_text(encoding="utf-8")
        for forbidden in ("preview_order(", "place_order(", "cancel_order("):
            if forbidden in rebalance_text:
                errors.append("REBALANCE SAFETY: read-only Rebalance may not call " + forbidden)
        for required in (
            'account_picker("risk_sizing_account")',
            "RESET TARGETS TO CURRENT",
            "APPLY DEFAULT BANDS",
            "REVIEW LOSS",
            "LONG-TERM TRIM REVIEW",
            "ANALYSIS ONLY",
        ):
            if required not in rebalance_text:
                errors.append("REBALANCE ROUTE MISSING: " + required)

    risk_live_ui = SRC / "risk_sizing_ui_v10.py"
    if risk_live_ui.exists():
        risk_live_text = risk_live_ui.read_text(encoding="utf-8")
        for required in (
            "client.preview_order(",
            "client.place_order(",
            "client.list_orders(",
            "client.cancel_order(",
            "REVIEW + SEND READY PROTECTIVE STOP",
            "SEND LIVE PROTECTIVE STOP",
            "5. PENDING / OPEN E*TRADE ORDERS",
            "6. PROTECTION WATCH LOG",
            "def maybe_auto_watch_risk_entries(",
        ):
            if required not in risk_live_text:
                errors.append(f"RISK LIVE ORDER FLOW MISSING: {required}")

        try:
            risk_tree = parse_file(risk_live_ui)
            watcher = next(
                (
                    node
                    for node in risk_tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "maybe_auto_watch_risk_entries"
                ),
                None,
            )
        except Exception as exc:
            watcher = None
            errors.append(f"RISK AUTO-WATCH GUARD COULD NOT PARSE WATCHER: {exc}")
        if watcher is None:
            errors.append("RISK AUTO-WATCH GUARD: maybe_auto_watch_risk_entries missing")
        else:
            watcher_source = ast.unparse(watcher)
            if "list_orders(" not in watcher_source:
                errors.append("RISK AUTO-WATCH GUARD: watcher must use read-only list_orders")
            for forbidden in (
                "preview_order(",
                "place_order(",
                "cancel_order(",
                "_preview_protective_stop(",
                "_place_reviewed_protective_stop(",
            ):
                if forbidden in watcher_source:
                    errors.append(
                        "RISK AUTO-WATCH GUARD: unattended watcher may not mutate orders: "
                        + forbidden
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
