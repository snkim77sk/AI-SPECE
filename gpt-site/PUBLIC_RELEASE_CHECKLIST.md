# G2B public-release gate

**Current state: preparation only. The existing ChatGPT Site remains owner-private.**
Do not flip public visibility or `G2B_PUBLIC_AUTH_ENABLED` solely because GitHub CI passed. The application authentication flag and the Sites audience are separate controls: authentication may be rehearsed in the existing owner-private Site before any public audience change.

## Member read-only rehearsal (5.2.23)

- The existing private owner can open `/dashboard?viewer=member` to inspect the member experience before social credentials are configured. The dedicated `viewer` parameter preserves existing business filters such as `view=current` or `view=history`.
- Preview is a privilege reduction. Data API calls carry the preview parameter, management routes and mutations are rejected, and anonymous or pending public accounts gain no access from it. Leaving preview restores only the authenticated owner's existing permissions.
- Approved members can search saved contracts, deliveries and budgets, view saved analysis results and download read-only exports. Collection, analysis creation/continuation, settings, member approval and operational diagnostics are administrator-only.
- A reviewed server allowlist protects member GET/HEAD routes. Unknown routes and all business mutations default to administrator-only. The public account/session/approval checks remain authoritative, independent of UI visibility.
- Member screens do not call collection or budget-workspace management APIs. Matching API reads are restricted to live jobs, and member responses omit worker leases, cursors and internal errors. Optional query caches may still be written; source data, RAW and collection checkpoints are not changed by data reads.
- This preview is not an external-provider login rehearsal or evidence that the Site is ready to become public. The existing owner-private audience and disabled public authentication stay unchanged during this code deployment.

## Auth providers and enrollment

- KakaoTalk and Daum unified account -> **Kakao Login** (one OAuth client); Daum is not a fourth OAuth identity provider.
- NAVER -> NAVER Login OAuth application.
- Gmail / Google -> Google OAuth / OpenID Connect client.
- Login uses authorization code, unpredictable state cookie (Secure/HttpOnly/SameSite=Lax), HTTPS fixed callback, short-lived nonce, Google PKCE, server-to-server token exchange and provider identity.
- User enters **pending** by default. Email never automatically links distinct social identities. Only a trusted administrator with a previously provisioned `admin` role can approve/reject/suspend. General member access is read-only; all collector writes and administrative endpoints stay restricted.
- Login cookies are `__Host-` scoped, Secure/HttpOnly, last 8 hours; only HMAC-hashed tokens are stored in existing D1. Every request checks current member approval, so suspension is effective without clearing browser cookies.
- No social OAuth access token, provider client secret, approval bootstrap value or sync transport token may appear in repository, request logs or browser storage.

## In-place migration

Use the existing Site D1 binding `DB` only. Verify additive migration `drizzle/0023_public_identity.sql` in a **non-production test database first** and confirm the existing Site's applied migration state before a separately authorized production migration. Migration 0022 records the two indexes already published by the private Site. Sites applies pending migrations during deployment. Do not recreate the database or rerun destructive initialization. Preserve all G2B rows, source payloads, receipts and applied migrations.

## Owner-private authentication rehearsal and first administrator

The first administrator cannot be required before the first verified social sign-in. Use this order; none of these steps is performed automatically by a readiness screen:

1. Confirm that the existing Site audience is **owner-private**. Keep that audience throughout rehearsal. Record the current working deployment and the supported way to restore its authentication configuration.
2. **Before enabling application authentication**, check that the existing Windows uploader supports the separate `X-G2B-Service-Token`, that its approved DPAPI configuration is prepared, and that rollback to the current private transport is available. Do not reuse the Sites owner bearer. Preserve schedules, source records and receipts. A configured server token alone does not prove Windows compatibility.
3. Check HTTPS origin, the session secret, service token and at least the provider used by the verified owner. Check the existing identity tables. The owner readiness screen distinguishes missing tables from a failed query. An absent administrator is expected at this step and must not disable first sign-in.
4. Under an explicit operational change, set `G2B_PUBLIC_AUTH_ENABLED=enabled` for a bounded **owner-private rehearsal**, keeping the native Site audience unchanged. Apply the configuration through the existing supported deployment flow. Immediately confirm Windows receipt GET/POST and status POST using the separate service token. Restore the previous working authentication configuration if transport fails; do not proceed through 401/403 errors.
5. Sign in with the verified owner's actual provider account. The application creates an ordinary **pending** member. An authorized operator must verify that provider identity and the exact member UUID through the existing trusted administrative channel. Do not trust an email-only match or allow the browser to nominate an administrator.
6. Use the explicit, audited first-administrator operation below. Require exactly one affected row; zero rows means the conditions were not met and requires inspection, not a broader retry. If an approved administrator already exists, this first-administrator procedure does nothing.
7. Start a fresh social login and verify administrator access, ordinary pending/approved/suspended access, session revocation, Windows receipts and rollback while the Site remains owner-private. Test every intended provider before public release.
8. Review actual evidence and separately authorize any native public audience change. No readiness flag, successful CI run, environment presence check or administrator count authorizes publication.

The first administrator operation uses the existing, supported and audited D1 administration surface, only after step 5 verifies the actual owner identity. The example is not an application endpoint and must not be executed with an unverified UUID:

```sql
-- Example only; replace the UUID after verifying the social provider identity and
-- the existing owner's signed-in session. Do not run against an unverified member.
UPDATE public_members
SET role='admin',status='approved',updated_at=CURRENT_TIMESTAMP,
    reviewed_at=CURRENT_TIMESTAMP,reviewed_by='operator-bootstrap'
WHERE id='VERIFIED_OWNER_MEMBER_UUID' AND status='pending' AND role='member'
  AND NOT EXISTS (SELECT 1 FROM public_members WHERE status='approved' AND role='admin');
```

No automatic email-based admin promotion exists. Do not enable external signups before this workflow and the member approval UI are verified in the owner-private rehearsal.

## Existing credentials and service bridge

Store in the existing Sites secret manager, not source:
- `G2B_PUBLIC_AUTH_ORIGIN`: exact HTTPS origin used for social redirect URLs (no path/query).
- `G2B_PUBLIC_SESSION_PEPPER`: independent cryptographically random high-entropy >=32-character secret; rotate by revoking sessions.
- `G2B_PUBLIC_SERVICE_SYNC_TOKEN`: separate strong >=32-character shared secret for the Windows verified upload client; **never reuse the ChatGPT Sites owner bearer**.
- `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`; callback: `https://YOUR_SITE/api/public-auth/callback/kakao`.
- `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`; callback: `https://YOUR_SITE/api/public-auth/callback/naver`.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`; callback: `https://YOUR_SITE/api/public-auth/callback/google`.

Kakao/Daum, NAVER and Google providers require their own registered application, exact redirect allowlist, consent-screen/review settings and client secrets. No provider credentials were installed or exposed during this development.

**Before public mode:** update and test the existing Windows `D:\G2B` verified uploader to send `X-G2B-Service-Token` from a separately secured DPAPI file. The updated `server/gpt_sync_client.py` accepts an **optional** `public_service_token_file` path in its existing local sync configuration; the file uses the original DPAPI-current-user-v1 encrypted container. When absent, the uploader retains its previous private-Sites request behavior. This code change does not create, install or read the token on `D:\\G2B`. Existing `OAI-Sites-Authorization` alone does NOT grant write access once public mode is enabled. Check that receipt GET/POST and server status POST work after the service-auth change; no HTTP 401/403 and no duplicate commits. Keep original Windows schedules and secrets unchanged until the dry-run/rollback plan is approved.

## Release gates — all required before changing visibility

1. Confirm active GPT Sites runtime/build, supported vinext `proxy.ts` protection, worker-first routing for every sensitive page/API, no public bypass for static assets or cache.
2. Verify `proxy.ts` and explicit backend guards reject unauthenticated requests on **all** existing data and mutation routes, not only the page menu. `GET /api/deliveries` and `GET /api/budget` must be 401 for guests / 403 for pending members. All POST mutations, collection status and settings must be forbidden for ordinary approved members.
3. Test all three provider callback flows end-to-end with real registered credentials: invalid state, expired state, mismatched redirect/nonce, denied consent, repeat callback, provider downtime, same email across two providers.
4. Test review: pending -> approved -> suspended -> rejected, including sessions already signed in. Admin bootstrap only via verified operator.
5. Apply migrations safely, test rollback and exact current rows, ensure user accounts are in the **existing** DB without loss.
6. Check the Windows uploader service token, server-side admission log, source receipts, G2B+LOFIN quota ledger, data freshness, and pending sync retries. Never let public login consume provider quotas.
7. Verify member data privacy, threat assessment, rate limiting/abuse prevention, cookie CSRF and incident escalation, Korean privacy notice/consent and retention policy.
8. Verify DNS, HTTPS, custom domain, OAuth provider allowed domain requirements and platform's explicit public-site capability.
9. Back up and document rollback to existing owner-private access; record sign-in or content access anomalies without exposing identity secrets.
10. Only after every requirement is signed off and the owner-private authentication rehearsal succeeds, separately authorize and perform the native Site audience change in a controlled release. Keep application authentication enabled and verified throughout public access. There is **no automatic public switch** in this repository.

## Read-only runtime readiness

- `/api/public-auth/readiness` exposes only service availability and the three providers' sign-in availability. KakaoTalk and Daum share one Kakao provider. It does not disclose secret values, origin URLs, member tables or administrator presence. A provider becomes available only when application authentication, base configuration, that provider's credential pair and existing identity storage are ready. An administrator is **not** required for first sign-in.
- `/api/release-readiness` is protected by the existing owner-private boundary and, when application public authentication is enabled, by the independently checked approved-administrator role. It reports only configuration booleans, identity schema state and approved-administrator presence. A failed database query is unknown, not a claim that no administrator exists.
- The owner screen labels availability as **login start conditions**, not successful OAuth. Real provider login, approval/suspension rehearsal, Windows service compatibility and public release remain **unverified**. No identities, environment values or database writes are part of these routes.
- Both screens bound the HTTP request and JSON body read to 15 seconds, clear old successful results during refresh, and offer a retry after failures. The readiness routes do not call OAuth providers or procurement sources, apply migrations, configure secrets, assign roles, change schedules or change Site access.

## Important exclusions

This PR does not make the G2B Site public, create new production DBs, install OAuth client secrets, authenticate a user in a real provider, install rate limiting, or change Windows server/NO1. It prepares a secured candidate; full production readiness needs external/provider and operation verification.

## Read-only preflight (added after v5.2.20 integration)

From `gpt-site/`, run `node scripts/public-release-preflight.mjs` in a **trusted operator environment only**. The result prints only check IDs and booleans, never secret contents, account emails, admin UUIDs or authorization headers. Exit code 1 means launch is blocked; code 0 means the explicitly provided configuration and evidence flags were supplied, **not** that GitHub or ChatGPT independently inspected the running Site.

The preflight requires secure configured origin, session pepper, distinct Windows sync service token, all three social OAuth client pairs, and explicit **operator-verified** evidence for the production D1 migration, initial owner admin, real Kakao/NAVER/Google sign-ins, approval/suspension permissions, sync receipt smoke tests, privacy/abuse reviews, rollback, and platform-supported public visibility. Evidence flags can be passed as `G2B_RELEASE_EVIDENCE_JSON` containing documented boolean keys (see `scripts/public-release-preflight.mjs`). Only mark an evidence key true after reviewing actual operator evidence; assertions alone are not external verification.

**Do not put any secret values, passwords, tokens or personal details into GitHub issues or ChatGPT prompts.** Use the authenticated Sites secret manager and existing Windows DPAPI storage. The script does not publish the Site, change visibility, install secrets, run D1 migrations, change Windows scheduled tasks, or contact any OAuth provider. A passing report still requires the human release review and official supported private-to-public publication flow.
