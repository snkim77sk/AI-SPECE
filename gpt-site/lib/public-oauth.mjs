import {PROVIDERS,validProvider,oauthCookie,stateValid} from './public-auth-policy.mjs';
const b64url=bytes=>btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/g,'');
const bytes=()=>crypto.getRandomValues(new Uint8Array(32));
const challenge=async verifier=>b64url(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
const secret=(env,name)=>typeof env[name]==='string'&&env[name].trim()?env[name]:null;
const credentials=(provider,env)=>{
 const config={kakao:['KAKAO_REST_API_KEY','KAKAO_CLIENT_SECRET'],naver:['NAVER_CLIENT_ID','NAVER_CLIENT_SECRET'],google:['GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET']}[provider];
 if(!config)throw Error('INVALID_PROVIDER');
 const id=secret(env,config[0]),secretValue=secret(env,config[1]);
 if(!id||!secretValue)throw Error('PROVIDER_NOT_CONFIGURED');
 return {id,secret:secretValue};
};
export async function oauthStart(provider,origin,env,now=Date.now()){
 if(!validProvider(provider))throw Error('INVALID_PROVIDER');
 const {id}=credentials(provider,env),state=b64url(bytes()),verifier=b64url(bytes()),nonce=b64url(bytes());
 const redirect=origin+'/api/public-auth/callback/'+provider;
 const params=new URLSearchParams({client_id:id,redirect_uri:redirect,response_type:'code',state});
 if(provider==='google'){
  params.set('scope','openid email profile');params.set('access_type','online');
  params.set('code_challenge',await challenge(verifier));params.set('code_challenge_method','S256');params.set('nonce',nonce);
 }
 const cookie=oauthCookie(provider)+'='+[state,verifier,nonce,String(now)].join('.')+'; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=600';
 return {url:PROVIDERS[provider].authorize+'?'+params,cookie};
}
export function verifyState(provider,queryState,header,now=Date.now()){
 if(!validProvider(provider))throw Error('INVALID_PROVIDER');
 const name=oauthCookie(provider),pair=(header||'').split(';').map(x=>x.trim()).find(x=>x.startsWith(name+'='));
 const stored=pair?.slice(name.length+1);
 if(!stateValid(queryState,stored,now))throw Error('OAUTH_STATE_INVALID');
 return stored.split('.')[1];
}
export function clearStateCookie(provider){
 return oauthCookie(provider)+'=; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=0';
}
async function fetchJson(url,options,limit=16384){
 const r=await fetch(url,{...options,redirect:'error',signal:AbortSignal.timeout(10000),cache:'no-store'});
 if(!r.ok)throw Error('PROVIDER_HTTP_ERROR');
 const len=Number(r.headers.get('content-length')||0);if(len>limit)throw Error('PROVIDER_RESPONSE_TOO_LARGE');
 const body=await r.text();if(new TextEncoder().encode(body).length>limit)throw Error('PROVIDER_RESPONSE_TOO_LARGE');
 try{return JSON.parse(body);}catch{throw Error('PROVIDER_INVALID_RESPONSE');}
}
export async function oauthIdentity(provider,code,verifier,origin,env){
 if(!validProvider(provider)||typeof code!=='string'||code.length<4||code.length>4096)throw Error('OAUTH_CODE_INVALID');
 const {id,secret:clientSecret}=credentials(provider,env);
 const body=new URLSearchParams({grant_type:'authorization_code',client_id:id,client_secret:clientSecret,redirect_uri:origin+'/api/public-auth/callback/'+provider,code});
 if(provider==='google')body.set('code_verifier',verifier);
 if(provider==='naver')body.set('state',''); // Naver token API requires the original state; caller overwrites below.
 const token=await fetchJson(PROVIDERS[provider].token,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()});
 if(typeof token?.access_token!=='string'||token.access_token.length<8||token.access_token.length>8192)throw Error('PROVIDER_TOKEN_INVALID');
 const profile=await fetchJson(PROVIDERS[provider].profile,{headers:{'Authorization':'Bearer '+token.access_token,'Accept':'application/json'}});
 let subject,name,email;
 if(provider==='kakao'){
  subject=String(profile?.id??'');name=profile?.properties?.nickname||profile?.kakao_account?.profile?.nickname||'';email=profile?.kakao_account?.is_email_verified===true?profile?.kakao_account?.email||'':'';
 }else if(provider==='naver'){
  if(profile?.resultcode!=='00')throw Error('PROVIDER_IDENTITY_INVALID');
  subject=String(profile?.response?.id??'');name=profile?.response?.name||profile?.response?.nickname||'';email=profile?.response?.email||'';
 }else{
  subject=String(profile?.sub??'');name=profile?.name||'';email=profile?.email_verified===true?profile?.email||'':'';
 }
 if(!/^[A-Za-z0-9_-]{1,256}$/.test(subject))throw Error('PROVIDER_IDENTITY_INVALID');
 return {provider,subject,name:typeof name==='string'?name.slice(0,120):'',email:typeof email==='string'?email.slice(0,254):''};
}
