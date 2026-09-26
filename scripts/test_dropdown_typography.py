"""Regression guard for terminal-wide native dropdown typography consistency."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = (ROOT / "src" / "theme.py").read_text(encoding="utf-8")
OPTION_BOOK = (ROOT / "src" / "option_book_ui.py").read_text(encoding="utf-8")
CORE = (ROOT / "src" / "terminal_core.py").read_text(encoding="utf-8")
RISK = (ROOT / "src" / "risk_sizing_ui_v2.py").read_text(encoding="utf-8")
RISK_V9 = (ROOT / "src" / "risk_sizing_ui_v9.py").read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


# Shared trigger coverage must handle both legacy BaseWeb and current React Aria
# Streamlit select markup.
require(THEME, '[data-testid="stSelectbox"] [data-baseweb="select"] > div *', "BaseWeb select trigger")
require(THEME, '[data-testid="stSelectbox"] [aria-haspopup="listbox"] *', "React Aria select trigger")
require(THEME, '[data-testid="stSelectbox"] [role="group"] *', "React Aria select group")
require(THEME, '[role="listbox"] [role="option"] *', "detached dropdown option")

# Closed selected values and opened menu values intentionally share the same
# Courier weight. This prevents the thin selected-value / heavy option mismatch.
theme_dropdown_start = THEME.index("TERMINAL-WIDE DROPDOWN READABILITY")
theme_dropdown_end = THEME.index("DATAFRAME COLUMN-MENU READABILITY")
theme_dropdown_css = THEME[theme_dropdown_start:theme_dropdown_end]
if theme_dropdown_css.count("font-weight:800 !important;") < 2:
    raise AssertionError("Shared dropdown trigger and options must both use weight 800")

# Hover/focus must never produce black text on a dark detached menu row.
# Cover both CSS pseudo states and React Aria's data-* interaction states.
for needle in (
    '[role="listbox"] [role="option"]:hover *',
    '[role="listbox"] [role="option"]:focus *',
    '[role="listbox"] [role="option"][data-hovered] *',
    '[role="listbox"] [role="option"][data-focused] *',
    '[role="listbox"] [role="option"][data-highlighted] *',
):
    require(theme_dropdown_css, needle, f"readable dropdown interaction selector {needle}")
require(
    theme_dropdown_css,
    "color:#ffad52 !important;",
    "shared dropdown hover foreground",
)

# Risk v9 renders after the shared theme and its detached-menu selectors are
# necessarily global. It must agree with the shared readable hover contract;
# only the actually selected orange row may use black text.
risk_option_start = RISK_V9.index('[role="option"]{')
risk_option_end = RISK_V9.index('[data-testid="stRadio"]', risk_option_start)
risk_option_css = RISK_V9[risk_option_start:risk_option_end]
require(risk_option_css, '[role="option"]:hover *', "Risk dropdown hover descendants")
require(risk_option_css, "color:#ffad52!important;", "Risk dropdown hover foreground")
if '[role="option"]:hover *{color:#000' in risk_option_css:
    raise AssertionError("Risk dropdown hover must never force black text")
require(
    risk_option_css,
    '[role="option"][aria-selected="true"] *{color:#000!important;',
    "selected orange dropdown row black text",
)

# Option Book had a stronger local select override. Keep only its selectbox text
# aligned to the shared dropdown weight without changing labels/number inputs.
select_start = OPTION_BOOK.index(
    '.st-key-option_book_workspace [data-testid="stSelectbox"] div[data-baseweb="select"] span,'
)
select_end = OPTION_BOOK.index(
    '.st-key-option_book_workspace [data-testid="stNumberInput"]',
    select_start,
)
option_select_css = OPTION_BOOK[select_start:select_end]
require(option_select_css, "font-weight:800!important;", "Option Book select weight")
if "font-weight:900!important;" in option_select_css:
    raise AssertionError("Option Book selectbox must not override shared weight with 900")

# Confirm the same shared E*TRADE picker still owns Holdings and Orders, while
# Risk receives that picker through dependency injection. No feature behavior is
# replaced for this presentation fix.
require(CORE, '_account_picker("holdings_account")', "Holdings shared account picker")
require(CORE, '_account_picker("orders_account")', "Orders shared account picker")
require(RISK, 'account_picker("risk_sizing_account")', "Risk shared account picker")



# The shared E*TRADE account picker must use one explicit typography contract
# across its label, React Aria closed value, and detached opened listbox rows.
require(
    THEME,
    '[data-testid="stSelectbox"] input[role="combobox"][aria-label="E*TRADE Account"]',
    "E*TRADE account combobox selector",
)
require(
    THEME,
    '[role="listbox"][aria-label="E*TRADE Account"] [role="option"] *',
    "E*TRADE account listbox selector",
)
account_block_start = THEME.index("E*TRADE ACCOUNT PICKER TYPOGRAPHY")
account_block_end = THEME.index("select:not(:disabled)", account_block_start)
account_css = THEME[account_block_start:account_block_end]
for declaration in (
    'font-family:"Courier New",Courier,monospace !important;',
    "font-size:14px !important;",
    "font-weight:700 !important;",
    "font-synthesis:none !important;",
    "text-rendering:geometricPrecision !important;",
):
    require(account_css, declaration, f"account typography declaration {declaration}")

print("dropdown typography regression: PASS")
