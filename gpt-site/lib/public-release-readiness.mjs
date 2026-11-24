import {baseAuthConfiguration,providerConfiguration} from './public-auth-config.mjs';

export const SIGNIN_AVAILABILITY_SCHEMA='g2b-signin-availability-v1';
export const RELEASE_READINESS_SCHEMA='g2b-release-readiness-v1';
const identityTables=['public_identities','public_members','public_sessions'];
const schemaSql="SELECT name FROM sqlite_master WHERE type='table' AND name IN ('public_identities','public_members','public_sessions') ORDER BY name";
// LIMIT 0 validates required columns without reading any member, identity or session values.
const columnsSql=`SELECT m.id,m.display_name,m.email,m.status,m.role,m.created_at,m.updated_at,m.reviewed_by,m.reviewed_at,
 i.provider,i.provider_user_id,i.member_id,i.created_at,s.token_hash,s.member_id,s.created_at,s.expires_at
 FROM public_members m,public_identities i,public_sessions s LIMIT 0`;

/** Existing DB only. Missing tables and failed queries are deliberately different states. */
export async function inspectIdentityStorage(getDatabase,{includeAdmin=false}={}){
 let db;
 try{
  db=typeof getDatabase==='function'?getDatabase():getDatabase;
  if(!db||typeof db.prepare!=='function')throw Error('UNAVAILABLE');
  const rows=(await db.prepare(schemaSql).all()).results;
  if(!Array.isArray(rows)||rows.some(row=>!identityTables.includes(row?.name)))throw Error('INVALID_RESULT');
  if(identityTables.some(name=>!rows.some(row=>row.name===name)))return {schema:'missing',approvedAdminPresent:null};
  const columns=(await db.prepare(columnsSql).all()).results;
  if(!Array.isArray(columns)||columns.length!==0)throw Error('INVALID_RESULT');
 }catch{return {schema:'unknown',approvedAdminPresent:null};}
 if(!includeAdmin)return {schema:'ready',approvedAdminPresent:null};
 try{
  const row=await db.prepare("SELECT EXISTS(SELECT 1 FROM public_members WHERE status='approved' AND role='admin') AS present").first();
  return {schema:'ready',approvedAdminPresent:row?.present===1?true:row?.present===0?false:null};
 }catch{return {schema:'ready',approvedAdminPresent:null};}
}

/** Anonymous output contains only sign-in availability, never schema or administrator facts. */
export async function signInAvailability(settings,getDatabase,checkedAt=new Date().toISOString()){
 const config=baseAuthConfiguration(settings),providers=providerConfiguration(settings);
 let state='disabled',storage=null;
 if(config.enabled){
  state='setup-required';
  if(config.configured&&providers.some(provider=>provider.configured)){
   storage=await inspectIdentityStorage(getDatabase);
   state=storage.schema==='ready'?'available':storage.schema==='unknown'?'unavailable':'setup-required';
  }
 }
 return {schema:SIGNIN_AVAILABILITY_SCHEMA,checkedAt,state,
  providers:providers.map(provider=>({id:provider.id,available:state==='available'&&provider.configured}))};
}

/** Configuration and SELECT evidence cannot attest real OAuth, approval rehearsal or public access. */
export async function releaseReadiness(settings,getDatabase,checkedAt=new Date().toISOString()){
 const config=baseAuthConfiguration(settings),providers=providerConfiguration(settings);
 const identityStorage=await inspectIdentityStorage(getDatabase,{includeAdmin:true});
 return {
  schema:RELEASE_READINESS_SCHEMA,checkedAt,enabled:config.enabled,
  configuration:{httpsOrigin:config.httpsOrigin,sessionSecret:config.sessionSecret,serviceSyncToken:config.serviceSyncToken},
  identityStorage,
  providers:providers.map(provider=>({...provider,
   loginAvailable:config.enabled&&config.configured&&identityStorage.schema==='ready'&&provider.configured,
   realLogin:'unverified'})),
  approvalFlow:'unverified',windowsServiceCompatibility:'unverified',publicRelease:'unverified',
 };
}
