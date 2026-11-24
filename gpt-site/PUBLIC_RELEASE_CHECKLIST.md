# G2B public-release gate (v5.2.19 candidate)

**Current state: preparation only. The existing ChatGPT Site remains owner-private.**
Do not flip public visibility or `G2B_PUBLIC_AUTH_ENABLED` solely because GitHub CI passed.

## Auth providers and enrollment

- KakaoTalk and Daum unified account -> **Kakao Login** (one OAuth client); Daum is not a fourth OAuth identity provider.
- NAVER -> NAVER Login OAuth application.
- Gmail / Google -> Google OAuth / OpenID Connect client.
- Login uses authorization code, unpredictable state cookie (Secure/HttpOnly/SameSite=Lax), HTTPS fixed callback, short-lived nonce, Google PKCE, server-to-server token exchange and provider identity.
- User enters **pending** by default. Email never automatically links distinct social identities. Only a trusted administrator with a previously provisioned `admin` role can approve/reject/suspend. General member access is read-only; all collector writes and administrative endpoints stay restricted.
- Login cookies are `__Host-` scoped, Secure/HttpOnly, last 8 hours; only HMAC-hashed tokens are stored in existing D1. Every request checks current member approval, so suspension is effective without clearing browser cookies.
- No social OAuth access token, provider client secret, approval bootstrap value or sync transport token may appear in repository, request logs or browser storage.

## In-place migration

Use the existing Site D1 binding `DB` only. Verify additive migration `drizzle/0023_public_identity.sql` in a **non-production test database first**. Migration 0022 records the two indexes already published by the private Site. Sites applies pending migrations during deployment. Keep 0023 out of private deployments until production membership tables are separately authorized. Preserve all G2B rows, source payloads, receipts and applied migrations.

After signing in for the first time, an operator with existing owner access must bootstrap one *verified* member as admin through the supported, audited D1 console using an exact member UUID, never a trusted email-only match:

```sql
-- Example only; replace the UUID after verifying the social provider identity and
-- the existing owner's signed-in session. Do not run against an unverified member.
UPDATE public_members
SET role='admin',status='approved',updated_at=CURRENT_TIMESTAMP,
    reviewed_at=CURRENT_TIMESTAMP,reviewed_by='operator-bootstrap'
WHERE id='VERIFIED_OWNER_MEMBER_UUID' AND status='pending' AND role='member';
```

No automatic email-based admin promotion exists. Do not enable external signups before this workflow and the member approval UI are verified in a non-public rehearsal.

## Existing credentials and service bridge

Store in the existing Sites secret manager, not source:
- `G2B_PUBLIC_AUTH_ORIGIN`: exact HTTPS origin used for social redirect URLs (no path/query).
- `G2B_PUBLIC_SESSION_PEPPER`: independent cryptographically random high-entropy >=32-character secret; rotate by revoking sessions.
- `G2B_PUBLIC_SERVICE_SYNC_TOKEN`: separate strong >=32-character shared secret for the Windows verified upload client; **never reuse the ChatGPT Sites owner bearer**.
- `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`; callback: `https://YOUR_SITE/api/public-auth/callback/kakao`.
- `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`; callback: `https://YOUR_SITE/api/public-auth/callback/naver`.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`; callback: `https://YOUR_SITE/api/public-auth/callback/google`.

Kakao/Daum, NAVER and Google providers require their own registered application, exact redirect allowlist, consent-screen/review settings and client secrets. No keys were installed or read during this development.

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
10. Only after every requirement is signed off, enable `G2B_PUBLIC_AUTH_ENABLED=enabled` in a supported authenticated deployment AND switch site visibility to public in a controlled release. There is **no automatic public switch** in this repository.

## Important exclusions

This PR does not make the G2B Site public, create new production DBs, install OAuth client secrets, authenticate a user in a real provider, install rate limiting, or change Windows server/NO1. It prepares a secured candidate; full production readiness needs external/provider and operation verification.
