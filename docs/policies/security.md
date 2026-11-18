This policy governs access to Raj's Terminal and the handling of financial data during personal hobbyist operation. Balraj Singh is the accountable security owner. Report security concerns to **enterpriseghotra@gmail.com** without including passwords, authenticator seeds, API tokens or financial payloads.

## Access control policy

Protected application access is denied by default. Each account is defined in a server-side identity registry with an enabled status, role, credential version and optional expiry. The owner controls provisioning and changes to that registry. Disabling or expiring an identity, or changing its security configuration, invalidates its existing application authorization on the next protected request.

The **owner** role may access financial features and their explicit review and confirmation controls. The **auditor** role may access the security and policy view and must not access financial accounts, Plaid linking or broker actions. Anonymous visitors may read public policies only. Do not create shared accounts for additional people; give each authorized person an individual identity and the least access required.

## Multi factor authentication and sessions

The keypad's first-factor verification is followed by a time-based authenticator code before protected access is granted. Authenticator apps that implement standard TOTP codes can be used without a paid service. Missing or invalid MFA configuration must leave the protected app locked. Public-source default access credentials are not valid production credentials.

Accepted authenticator counters and failed attempts are recorded atomically in a private encrypted authentication ledger. A previously accepted code cannot be accepted again. Session authorization is checked against the current identity configuration, role and expiry before account or broker session restoration. Sessions have bounded idle and absolute lifetimes.

Touch ID uses platform WebAuthn with server-side challenge, origin and user-verification checks. It must not bypass the central authenticator requirement. New passkey enrollment requires a fresh complete authentication. The Terminal never receives a raw biometric template or passkey private key.

## Administrative systems and service identities

MFA is required on GitHub, hosting and Plaid administrative accounts. The owner must complete provider enrollment and retain recovery material securely. Application MFA does not prove that those provider accounts have MFA enabled. Service credentials must be restricted to the necessary resources, kept server-side and revoked or rotated when access is no longer required or compromise is suspected.

An identity's configured disablement or expiry automatically removes its application access. Provider accounts, repository memberships, service tokens and other external access must also be revoked through their own administrative systems. The project currently has no employee directory or HR integration; automatic HR-driven external-account offboarding is not claimed.

## Encryption and boundaries

The hosted application and service API connections use HTTPS. Networth persists financial data and Plaid tokens as authenticated encrypted documents and verifies that the storage repository is private. Authentication state uses a separate private storage scope. Do not place credentials, financial data or sensitive authentication evidence in source code, public pages, URLs or routine logs.

Application requests are revalidated rather than trusted solely because an earlier session was unlocked. This application-level boundary does not claim enterprise network segmentation or a complete organization-wide zero trust deployment.

## Reviews incident response and exceptions

Review identities, roles, expiry dates, provider memberships and token scopes at least quarterly and after an access change or incident. Record who reviewed the access, the date, the resources checked, findings and completed revocations. A scheduled review request is not evidence that the review has occurred.

For suspected compromise, contain affected access, stop affected collection if necessary, preserve sanitized evidence, assess the scope, rotate compromised credentials and verify recovery. Notify Plaid and affected parties as required by the applicable agreement and law. Record the timeline and corrective actions. Exceptions require an owner, compensating controls and an expiry date.

## Related policies and review

Follow the [privacy notice](/?policy=privacy), [retention procedure](/?policy=retention), and [vulnerability-management policy](/?policy=vulnerability-management). Review this policy quarterly and after material architecture or provider changes. Requirements must be supported by operational evidence before they are represented as implemented in a third-party attestation.
