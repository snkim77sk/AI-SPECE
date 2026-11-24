/**
 * Public-release authorization policy for the existing G2B Site.
 * Activation is opt-in. The existing owner-private site remains unchanged.
 * Never rely on client UI hiding or a social provider's email for data access.
 */
export const PROVIDERS=Object.freeze({
  kakao:{label:'카카오톡·다음',authorize:'https://kauth.kakao.com/oauth/authorize',token:'https://kauth.kakao.com/oauth/token',profile:'https://kapi.kakao.com/v2/user/me'},
  naver:{label:'네이버',authorize:'https://nid.naver.com/oauth2.0/authorize',token:'https://nid.naver.com/oauth2.0/token',profile:'https://openapi.naver.com/v1/nid/me'},
  google:{label:'구글·Gmail',authorize:'https://accounts.google.com/o/oauth2/v2/auth',token:'https://oauth2.googleapis.com/token',profile:'https://openidconnect.googleapis.com/v1/userinfo'},
});
export const SESSION_COOKIE='__Host-g2b_public_session';
export const SESSION_TTL_SECONDS=8*60*60;
export const OAUTH_TTL_SECONDS=600;
export const oauthCookie=provider=>'__Host-g2b_oauth_'+provider;
export const validProvider=provider=>Object.hasOwn(PROVIDERS,provider);
export function configuredOrigin(value){
  if(typeof value!=='string')return null;
  try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.search&&!u.hash&&(u.pathname==='/'||u.pathname==='')?u.origin:null;}
  catch{return null;}
}
export function sameOrigin(request,expected){
  const o=request.headers.get('origin');
  return !!expected&&o===expected&&new URL(request.url).origin===expected;
}
export function cookieValue(header,key){
  if(typeof header!=='string'||!header)return null;
  const piece=header.split(';').map(x=>x.trim()).find(x=>x.startsWith(key+'='));
  const v=piece?.slice(key.length+1);
  return v&&/^[A-Za-z0-9_.~-]{1,512}$/.test(v)?v:null;
}
export function permanentStatus(role,status){
  return role==='admin'&&status==='approved'?'admin':status==='approved'?'member':status==='pending'?'pending':'denied';
}
const pathIs=(path,prefix)=>path===prefix||path.startsWith(prefix+'/');
/**
 * Returns allow/unauthorized/forbidden/service. All unknown paths are protected.
 * Only approved administrators can write or see admin/collector configuration.
 */
export function accessDecision(path,method,principal,service=false){
  const m=String(method||'GET').toUpperCase();
  if(!['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'].includes(m))return 'forbidden';
  if(pathIs(path,'/api/public-auth/admin')||pathIs(path,'/api/release-readiness')||pathIs(path,'/admin')||pathIs(path,'/owner-progress'))return principal?.status==='approved'&&principal?.role==='admin'?'allow':principal?'forbidden':'unauthorized';
  if(path==='/api/public-auth/readiness'&&['GET','HEAD'].includes(m))return 'allow';
  if(path==='/login'||path==='/account/pending'||path==='/api/public-auth/me'||path==='/api/public-auth/logout'
     ||/^\/api\/public-auth\/(?:start|callback)\/(?:kakao|naver|google)$/.test(path))return 'allow';
  if(path==='/favicon.ico'||path.startsWith('/_next/static/')||path.startsWith('/_next/image/'))return 'allow';
  if(path==='/robots.txt')return 'allow';
  if((path==='/api/server-sync'||path==='/api/server-status')&&service&&['GET','POST'].includes(m))return 'service';
  if(!principal)return 'unauthorized';
  if(principal.status!=='approved')return 'forbidden';
  if(pathIs(path,'/admin')||pathIs(path,'/api/automation')||pathIs(path,'/settings')||pathIs(path,'/api/collection')||pathIs(path,'/api/collection-status')||pathIs(path,'/collection-monitor')||pathIs(path,'/api/maintenance')||pathIs(path,'/api/trial')||pathIs(path,'/api/server-sync')||pathIs(path,'/api/server-status')||pathIs(path,'/api/storage-inspection')||pathIs(path,'/storage-inspection')){
    return principal.role==='admin'?'allow':'forbidden';
  }
  if(m==='GET'||m==='HEAD')return 'allow';
  return principal.role==='admin'?'allow':'forbidden';
}
export const safeErrorResponse=(status,code)=>Response.json({error:code},{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export function stateValid(state,cookie,now=Date.now()){
  if(!/^[A-Za-z0-9_-]{32,100}$/.test(state||'')||typeof cookie!=='string')return false;
  const parts=cookie.split('.');
  if(parts.length!==4||parts[0]!==state)return false;
  if(!/^[A-Za-z0-9_-]{32,100}$/.test(parts[1])||!/^[A-Za-z0-9_-]{32,100}$/.test(parts[2])||!/^\d{13}$/.test(parts[3]))return false;
  const t=Number(parts[3]);return t<=now+30000&&now-t<=OAUTH_TTL_SECONDS*1000;
}
export const publicAuthReady=env=>env?.G2B_PUBLIC_AUTH_ENABLED==='enabled'&&!!configuredOrigin(env?.G2B_PUBLIC_AUTH_ORIGIN);
