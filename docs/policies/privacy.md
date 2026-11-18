Raj's Terminal is a personal financial dashboard operated by Balraj Singh. It is currently used for personal development and financial tracking and is not offered as a commercial customer service. This notice describes the information processed by its Plaid Networth integration and the choices available to the account owner.

## Information processed

After the account owner deliberately starts Plaid Hosted Link and authorizes accounts, the integration receives Plaid connection tokens and institution and account metadata. It reads account names, identifiers and masks, account types, currencies, current balances and available update timestamps. The owner may add account nicknames and asset or liability classifications. The app records dated balance-sheet observations and offers owner-requested exports.

The Terminal does not ask for a bank password in its own input fields. Authentication to a financial institution occurs in Plaid Hosted Link or the institution's authorization flow. Plaid's and the institution's applicable terms and privacy notices also govern their processing.

## Purpose and consent

Information is used to display personal account balances, classify assets and liabilities, calculate net worth, retain historical observations and produce requested exports. Linking an account is optional. Only accounts deliberately authorized by the account owner are connected. Product and institution availability depend on Plaid and the financial institution.

The Terminal does not sell, license or provide Plaid-derived data to advertisers or unrelated commercial users. Before any commercial launch, the operator must reassess the data flow and notices and complete the appropriate Plaid business review.

## Hosting and service providers

Streamlit Community Cloud hosts the application. Plaid and the relevant financial institution support account access. GitHub supports application development, scheduled workers and private encrypted storage of Networth records. These providers process information to deliver their services under their applicable agreements and privacy notices.

Financial records and Plaid tokens saved by Networth are encrypted before storage. API credentials and encryption material remain server-side. The application requires configured multi-factor authentication for protected access and remains locked if the required authentication configuration is missing. Public policy pages contain no account data and do not require sign-in.

## Retention and disconnection

The [retention and deletion policy](/?policy=retention) describes current-record removal, optional retention cleanup, and the separate treatment of earlier encrypted storage revisions, backups and downloaded exports. Disconnecting a financial institution revokes its active Plaid connection and stops future syncs. It does not by itself erase every historical copy.

## Access correction and deletion requests

The account owner may review available records, export history, correct nicknames and classifications, and disconnect institutions. Send privacy or deletion requests to **enterpriseghotra@gmail.com**. The operator will verify the requester's authority using minimal additional information and identify the requested data and copies. Do not send bank passwords, authenticator seeds, API tokens or full account numbers by email.

The operator must record the request, stop further collection where appropriate, carry out the supported removal steps, explain any remaining provider-held or historical copies, and verify the outcome before reporting completion. Applicable rights, exceptions and deadlines depend on the request and governing law. This notice does not claim that a partial removal is complete erasure.

## Contact and changes

Contact Balraj Singh, project owner, at **enterpriseghotra@gmail.com** for privacy and security matters. Review this notice quarterly, after a material change to data use, and before offering the application to external customers. Material changes must be reflected in the published notice. The first review target for this version is February 17, 2027.
