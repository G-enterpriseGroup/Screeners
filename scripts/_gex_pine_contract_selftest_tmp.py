#!/usr/bin/env python3
"""Temporary branch-only self-test; deleted before merge."""
from __future__ import annotations

import ast
import re
from pathlib import Path

source = Path('src/gex_ui_v3.py').read_text(encoding='utf-8')
ast.parse(source, filename='src/gex_ui_v3.py')

allowed = re.compile(r'^(?:SPOT|GFLIP|CALLWALL|PUTWALL|MAXCALLOI|MAXPUTOI|GEXPOS\d+|GEXNEG\d+)$')
compact = '''Ticker: SPY
SPOT,757.2477,0
GFLIP,758.83,0
CALLWALL,761.67,12345
PUTWALL,754.10,-12345
MAXCALLOI,760,5000
MAXPUTOI,754,12000
GEXNEG1,757,-1000
GEXPOS2,760,900'''
verbose = '''Ticker: SPY
Mode: BARCHART_STYLE
Spot: 757.2477
Max DTE Used: 45
Contracts Used: 2658
Net Current GEX: -18792600209.85
Source URL: E*TRADE API /v1/market/optionchains
PASTE EVERYTHING BELOW INTO PINE INPUT:
Packed Gamma Levels
SPOT,757.2477,0'''

def shape_ok(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r'Ticker:\s*[^\r\n]+', line, flags=re.I):
            continue
        parts = [part.strip() for part in line.replace('|', ',').split(',')]
        if len(parts) != 3 or not allowed.fullmatch(parts[0].upper()):
            return False
        float(parts[1]); float(parts[2])
    return True

assert shape_ok(compact)
assert not shape_ok(verbose)
assert 'gexv3_bridge_choice_pine_exact_v4' in source
assert '_proven._render_tradingview_pine = _render_tradingview_pine_exact' in source
assert '_proven._render_tradingview_pine = original_tradingview' in source
print('GEX Pine exact self-test: PASS')
