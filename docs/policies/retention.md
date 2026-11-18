This policy applies to Plaid connection records, current Networth accounts, balance-sheet history and owner exports. Balraj Singh is accountable for implementation and requests at **enterpriseghotra@gmail.com**.

## Retention and collection

Retain active connection tokens only while the account owner authorizes the connection and it serves the personal tracking purpose. Review retained information at least quarterly. Record the purpose for retained history and remove unnecessary current records using the supported controls.

Time-based cleanup is an explicit owner configuration. Until the owner enables a retention interval, historical observations do not automatically expire. A configured interval governs eligible daily documents in the current private ledger; maintenance must record actual verified removals and report failures rather than silently claim success. No past review or deletion run is implied by publishing this policy.

## Disconnection and current record removal

Disconnecting an institution revokes its active Plaid Item, removes its current connection and account records, and stops future syncing. Separate history-deletion controls must require an explicit owner confirmation and clearly describe the date range and scope. Maintenance must coordinate with active sync/link work so an older job cannot silently recreate deleted current records.

Deletion and retention actions must clear affected application caches and verify the resulting current state. If the authoritative deletion state cannot be verified, a stale financial snapshot must not be represented as current or erased data restored as an outage fallback.

## Earlier revisions backups and exports

Current-document deletion is not complete historical erasure. Older encrypted Git revisions, provider backups, clones and downloaded exports may still contain copies. The operator must inventory these copies, restrict access and address them separately for a full erasure request.

Deleting a file in a new Git commit alone does not remove earlier revisions. Full historical erasure may require provider-assisted cleanup or verified retirement of every copy of a ledger-exclusive encryption key, with separate handling for plaintext exports. Do not destroy a key shared with other retained data, and do not report complete erasure while recovery material or relevant exports remain available.

## Request and verification procedure

1. Verify that the requester is the authorized account owner and record the request date, scope and applicable deadline without logging raw credentials or full account numbers.
2. Stop further collection where appropriate and identify current records, snapshots, earlier revisions, backups and exports in scope.
3. Revoke active Plaid access as requested and use the supported current-record/history removal controls. Record verified results and any failed or interrupted work.
4. Address historical/provider copies and exports separately. Record limitations, lawful retention and any provider-assisted or cryptographic-erasure steps actually completed.
5. Communicate the verified outcome accurately, including any records or copies that remain. Retain a minimal request receipt without the removed financial payload.

## Review and operating evidence

Review this procedure quarterly and after a change in storage, encryption-key management or data flow. Retain dated maintenance and request receipts. A written policy or a successful current-record removal does not by itself prove a fully enforced, periodically reviewed, comprehensive erasure program.
