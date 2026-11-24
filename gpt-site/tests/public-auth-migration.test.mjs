import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';

test('public identity migration works as D1 prepared statements and preserves existing records',()=>{
 const db=new DatabaseSync(':memory:');
 try{
  db.exec("PRAGMA foreign_keys=ON; CREATE TABLE existing_source_receipts(id TEXT PRIMARY KEY, payload TEXT NOT NULL); INSERT INTO existing_source_receipts VALUES('kept','original');");
  const migration=readFileSync(new URL('../drizzle/0023_public_identity.sql',import.meta.url),'utf8');
  // Drizzle D1 prepares each chunk independently; sqlite.exec would hide a missing breakpoint.
  const apply=()=>{for(const statement of migration.split('--> statement-breakpoint').filter(s=>s.trim()))db.prepare(statement).run();};
  apply();
  db.prepare("INSERT INTO public_members(id,display_name,created_at,updated_at) VALUES('m','회원','2026-11-25','2026-11-25')").run();
  db.prepare("INSERT INTO public_identities(provider,provider_user_id,member_id,created_at) VALUES('google','u','m','2026-11-25')").run();
  db.prepare("INSERT INTO public_sessions(token_hash,member_id,created_at,expires_at) VALUES('digest','m',1,2)").run();
  apply();
  assert.equal(db.prepare('SELECT payload FROM existing_source_receipts WHERE id=?').get('kept').payload,'original');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM public_members').get().n,1);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM public_identities').get().n,1);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM public_sessions').get().n,1);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='index' AND name IN ('idx_public_identity_member','idx_public_members_review','idx_public_sessions_member','idx_public_sessions_expiry')").get().n,4);
 }finally{db.close();}
});
