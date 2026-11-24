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
// Approved members can only read these reviewed data routes. A new route is
// administrative until it is deliberately added to this list.
export const MEMBER_READ_PATHS=Object.freeze([
  '/', '/dashboard', '/shopping', '/smartled', '/vendors', '/budget',
  '/budget/project', '/budget/history', '/budget/observations', '/matching',
  '/institution-patterns', '/future-ranking', '/future-evidence', '/contract-review',
  '/api/dashboard', '/api/deliveries', '/api/facets', '/api/analysis',
  '/api/budget', '/api/budget/export', '/api/budget/history', '/api/budget/navigation',
  '/api/budget/observations', '/api/budget/project', '/api/budget/structure',
  '/api/budget/summary', '/api/matching', '/api/institution-patterns',
  '/api/future-ranking', '/api/future-evidence',
]);
const memberReads=new Set(MEMBER_READ_PATHS);
export const memberPreviewRequested=request=>new URL(request.url).searchParams.getAll('viewer').includes('member');
/**
 * Returns allow/unauthorized/forbidden/service. All unknown paths are protected.
 * Only approved administrators can write or see admin/collector configuration.
 */
export function accessDecision(path,method,principal,service=false){
  path=String(path||'/').replace(/\/+$/,'')||'/';
  const m=String(method||'GET').toUpperCase();
  const read=m==='GET'||m==='HEAD';
  if(!['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'].includes(m))return 'forbidden';
  if(pathIs(path,'/api/public-auth/admin')||pathIs(path,'/api/release-readiness')||pathIs(path,'/admin')||pathIs(path,'/owner-progress'))return principal?.status==='approved'&&principal?.role==='admin'?'allow':principal?'forbidden':'unauthorized';
  if(read&&(path==='/api/public-auth/readiness'||path==='/login'||path==='/account/pending'||path==='/api/public-auth/me'
     ||/^\/api\/public-auth\/(?:start|callback)\/(?:kakao|naver|google)$/.test(path)))return 'allow';
  if(path==='/api/public-auth/logout'&&m==='POST')return 'allow';
  if(read&&(path==='/favicon.ico'||path==='/favicon.svg'||path==='/robots.txt'||path.startsWith('/_next/static/')||pathIs(path,'/_next/image')))return 'allow';
  if((path==='/api/server-sync'||path==='/api/server-status')&&service&&['GET','POST'].includes(m))return 'service';
  if(!principal)return 'unauthorized';
  if(principal.status!=='approved')return 'forbidden';
  if(principal.role==='admin')return 'allow';
  return principal.role==='member'&&read&&memberReads.has(path)?'allow':'forbidden';
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
