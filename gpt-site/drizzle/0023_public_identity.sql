-- Additive tables within the EXISTING G2B D1 DB; no new database or migration of source rows.
CREATE TABLE IF NOT EXISTS public_members (
 id TEXT PRIMARY KEY NOT NULL,
 display_name TEXT NOT NULL,
 email TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending' CONSTRAINT public_member_status CHECK(status IN ('pending','approved','rejected','suspended')),
 role TEXT NOT NULL DEFAULT 'member' CONSTRAINT public_member_role CHECK(role IN ('member','admin')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 reviewed_by TEXT,
 reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS public_identities (
 provider TEXT NOT NULL CONSTRAINT public_identity_provider CHECK(provider IN ('kakao','naver','google')),
 provider_user_id TEXT NOT NULL,
 member_id TEXT NOT NULL REFERENCES public_members(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(provider,provider_user_id)
);
CREATE INDEX IF NOT EXISTS idx_public_identity_member ON public_identities(member_id);
CREATE INDEX IF NOT EXISTS idx_public_members_review ON public_members(status,created_at);
CREATE TABLE IF NOT EXISTS public_sessions (
 token_hash TEXT PRIMARY KEY NOT NULL,
 member_id TEXT NOT NULL REFERENCES public_members(id),
 created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_public_sessions_member ON public_sessions(member_id);
CREATE INDEX IF NOT EXISTS idx_public_sessions_expiry ON public_sessions(expires_at);
