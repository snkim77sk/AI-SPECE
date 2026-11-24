/**
 * Read-only public release preflight. Never prints credentials or changes Sites/Windows.
 * Operator evidence is explicit; local configuration alone is not proof of deployment.
 * Usage: node scripts/public-release-preflight.mjs
 * Optional manual attestations: G2B_RELEASE_EVIDENCE_JSON='{"...":true}'
 */
import {pathToFileURL} from 'node:url';
import {configuredOrigin} from '../lib/public-auth-policy.mjs';

export const EVIDENCE_CHECKS=Object.freeze([
 'private_site_deployed_and_rollback_tested',
 'existing_d1_migration_0023_verified',
 'verified_initial_admin_bootstrap',
 'kakao_real_oauth_passed',
 'naver_real_oauth_passed',
 'google_real_oauth_passed',
 'pending_approved_suspended_authz_tested',
 'public_server_sync_token_verified',
 'windows_receipts_and_data_freshness_verified',
 'privacy_notice_rate_limit_and_incident_reviewed',
 'public_visibility_supported_and_approved',
]);

const validSecret=(value,min=32)=>typeof value==='string'&&value.length>=min&&value.trim().length>=min;
const hasValue=value=>typeof value==='string'&&value.trim().length>0;

export function assessPublicRelease(env={},evidence={}){
 const origin=configuredOrigin(env.G2B_PUBLIC_AUTH_ORIGIN);
 const config=[
  ['origin_https',Boolean(origin)],
  ['session_pepper',validSecret(env.G2B_PUBLIC_SESSION_PEPPER)],
  ['windows_service_sync_token',validSecret(env.G2B_PUBLIC_SERVICE_SYNC_TOKEN)],
  ['kakao_application',hasValue(env.KAKAO_REST_API_KEY)&&hasValue(env.KAKAO_CLIENT_SECRET)],
  ['naver_application',hasValue(env.NAVER_CLIENT_ID)&&hasValue(env.NAVER_CLIENT_SECRET)],
  ['google_application',hasValue(env.GOOGLE_CLIENT_ID)&&hasValue(env.GOOGLE_CLIENT_SECRET)],
 ];
 const checks=[
  ...config.map(([id,ok])=>({id,verified:ok===true})),
  ...EVIDENCE_CHECKS.map(id=>({id,verified:evidence?.[id]===true})),
 ];
 const blockers=checks.filter(x=>!x.verified).map(x=>x.id);
 // Never set the flag or site visibility in preflight. Already-enabled is not
 // evidence of release readiness, and we deliberately never print its value.
 return {ready:blockers.length===0,checks,blockers,requires_manual_site_publication:true};
}

function parseEvidence(raw){
 if(!raw)return {};
 try{
  const value=JSON.parse(raw);
  return value&&typeof value==='object'&&!Array.isArray(value)?value:{};
 }catch{return {};}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 const report=assessPublicRelease(process.env,parseEvidence(process.env.G2B_RELEASE_EVIDENCE_JSON));
 process.stdout.write(JSON.stringify(report,null,2)+'\n');
 process.exitCode=report.ready?0:1;
}
