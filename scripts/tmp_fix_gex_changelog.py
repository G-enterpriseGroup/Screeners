from pathlib import Path

path = Path('src/TERMINAL_CHANGELOG.md')
text = path.read_text()
marker = '\n## 2026-09-16 — Quote-wrap TradingView GEX transport\n'
if marker not in text:
    raise SystemExit('Quoted GEX changelog marker not found')
head = text.split(marker, 1)[0].rstrip()
entry = r'''

## 2026-09-16 — Quote-wrap TradingView GEX transport

- **Feature changed:** GEX TradingView / Pine `Packed Gamma Levels` copy transport.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The compact Pine-router payload had the correct internal ticker headers and packed rows but was copied as bare multiline text. The working TradingView input requires the whole paste to be one quoted string value, with an opening double quote before the first `Ticker:` and a closing double quote immediately after the final packed row.
- **Root cause:** The prior router fix corrected the internal row grammar but omitted the outer Pine string delimiters shown by the working paste.
- **What was changed:** Pine-safe MASTER and single-ticker copy payloads are now wrapped exactly once with one leading and one trailing double quote. Parser validation still runs on the unquoted internal payload, and a separate check rejects a Pine-safe copy payload missing either outer quote. The Google Sheets full/reference block remains unchanged. Visible build bumped to `v2026.09.17.07`.
- **Important behavior that must remain:** Pine-safe output begins with `"Ticker: SYMBOL` and ends with the final packed-row value followed immediately by `"`. Do not quote each line. Preserve the internal `Ticker:` plus `SPOT/GFLIP/CALLWALL/PUTWALL/MAXCALLOI/MAXPUTOI/GEXPOS*/GEXNEG*` contract exactly.
- **Files/features intentionally NOT changed:** GEX calculations, E*TRADE request/session plumbing, Refresh All concurrency/rate limits, IV Rank, Google Sheets full/reference serialization, the TradingView Pine script, Risk Sizing, OAuth, Holdings, navigation, Bull Debit, Muni, Orders, shared theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Quote-contract static assertions; Python syntax compilation for changed GEX production files; `python scripts/validate_architecture.py`; PR changed-file inspection; post-merge architecture guard required.
- **Architecture guard result:** PASS on staging before code commit.
- **Commit SHA:** `e456f1fb4b0bd25e1b22226d92c54742843f8b57` (production code stage); final merge SHA recorded by GitHub after merge.
- **Lesson:** The TradingView contract has two layers: parser-native packed rows internally and one pair of outer double quotes around the complete multiline paste externally.
'''
path.write_text(head + entry)
