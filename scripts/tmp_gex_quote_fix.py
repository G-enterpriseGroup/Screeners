from pathlib import Path
import subprocess

ui = Path('src/gex_ui_v3.py')
text = ui.read_text()

old = '''    byte_count = len(text.encode("utf-8"))
    headers = _proven._pine_router_headers(text)
    missing_headers = [ticker for ticker in expected if ticker not in headers]
    pine_issues, parsed_counts = _proven._pine_master_issues(text, expected)
    too_large = byte_count > _proven._PINE_TEXT_LIMIT
    transport_ok = not missing_headers and not pine_issues and not too_large
'''
new = '''    # TradingView/Pine treats the complete pasted multiline transport as one
    # string value only when it has one opening and one closing double quote.
    # Keep parser checks on the unquoted payload so the internal router grammar
    # remains exact: Ticker: headers plus packed rows only.
    parser_text = text
    if not is_full:
        text = f'"{parser_text.strip()}"'

    byte_count = len(text.encode("utf-8"))
    headers = _proven._pine_router_headers(parser_text)
    missing_headers = [ticker for ticker in expected if ticker not in headers]
    pine_issues, parsed_counts = _proven._pine_master_issues(parser_text, expected)
    too_large = byte_count > _proven._PINE_TEXT_LIMIT
    quoted_transport_ok = is_full or (text.startswith('"') and text.endswith('"'))
    transport_ok = not missing_headers and not pine_issues and not too_large and quoted_transport_ok
'''
if old not in text:
    raise SystemExit('Expected Pine validation block not found')
text = text.replace(old, new, 1)

old = '''        if too_large:
            details.append("TEXT_LIMIT")
        st.error("PINE ROUTER CHECK FAILED // " + ", ".join(details[:16]))
'''
new = '''        if too_large:
            details.append("TEXT_LIMIT")
        if not quoted_transport_ok:
            details.append("MISSING_OUTER_DOUBLE_QUOTES")
        st.error("PINE ROUTER CHECK FAILED // " + ", ".join(details[:16]))
'''
if old not in text:
    raise SystemExit('Expected failure block not found')
text = text.replace(old, new, 1)

old = '            "**COPY THIS BLOCK INTO GEX TEST → Packed Gamma Levels. It intentionally contains only `Ticker:` headers and packed rows.**"\n'
new = '            "**COPY THIS ENTIRE BLOCK INTO GEX TEST → Packed Gamma Levels. The opening and closing double quotes are required and are already included.**"\n'
if old not in text:
    raise SystemExit('Expected master instruction not found')
text = text.replace(old, new, 1)

old = '        st.markdown("**COPY THIS SINGLE-TICKER BLOCK INTO GEX TEST → Packed Gamma Levels.**")\n'
new = '        st.markdown("**COPY THIS ENTIRE SINGLE-TICKER BLOCK INTO GEX TEST → Packed Gamma Levels. Keep the opening and closing double quotes.**")\n'
if old not in text:
    raise SystemExit('Expected single-ticker instruction not found')
text = text.replace(old, new, 1)
ui.write_text(text)

workspace = Path('src/gex_workspace_v2.py')
ws = workspace.read_text()
old_version = 'GEX_BUILD_VERSION = "v2026.09.17.06"'
new_version = 'GEX_BUILD_VERSION = "v2026.09.17.07"'
if old_version not in ws:
    raise SystemExit('Expected build version not found')
workspace.write_text(ws.replace(old_version, new_version, 1))

subprocess.run(['python', '-m', 'py_compile', 'src/gex_ui_v3.py', 'src/gex_workspace_v2.py'], check=True)
subprocess.run(['python', 'scripts/validate_architecture.py'], check=True)
