import {configuredOrigin} from './public-auth-policy.mjs';

export const AUTH_PROVIDER_IDS=Object.freeze(['kakao','naver','google']);
export const PROVIDER_CREDENTIAL_KEYS=Object.freeze({
 kakao:Object.freeze(['KAKAO_REST_API_KEY','KAKAO_CLIENT_SECRET']),
 naver:Object.freeze(['NAVER_CLIENT_ID','NAVER_CLIENT_SECRET']),
 google:Object.freeze(['GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET']),
});
const present=value=>typeof value==='string'&&value.trim().length>0;
export const validAuthSecret=value=>typeof value==='string'&&value.length>=32&&value.trim().length>=32;

/** Only booleans leave this boundary; credentials, origin and identities never do. */
export function baseAuthConfiguration(settings={}){
 const httpsOrigin=Boolean(configuredOrigin(settings.G2B_PUBLIC_AUTH_ORIGIN));
 const sessionSecret=validAuthSecret(settings.G2B_PUBLIC_SESSION_PEPPER);
 const serviceSyncToken=validAuthSecret(settings.G2B_PUBLIC_SERVICE_SYNC_TOKEN);
 return {enabled:settings.G2B_PUBLIC_AUTH_ENABLED==='enabled',httpsOrigin,sessionSecret,serviceSyncToken,
  configured:httpsOrigin&&sessionSecret&&serviceSyncToken};
}

export function providerConfiguration(settings={}){
 return AUTH_PROVIDER_IDS.map(id=>({id,configured:PROVIDER_CREDENTIAL_KEYS[id].every(key=>present(settings[key]))}));
}
