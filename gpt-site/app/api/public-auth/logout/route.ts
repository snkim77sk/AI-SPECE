import {revokeSession,expiredSessionCookie,publicEnabled,publicConfigured,publicOrigin} from '../../../../lib/public-auth-service';
export const dynamic='force-dynamic';
export async function POST(request:Request){
 if(!publicEnabled()||!publicConfigured())return Response.json({error:'PUBLIC_AUTH_NOT_READY'},{status:503});
 const origin=publicOrigin();
 if(!origin||request.headers.get('origin')!==origin||new URL(request.url).origin!==origin)return Response.json({error:'ORIGIN_NOT_ALLOWED'},{status:403});
 try{await revokeSession(request);}catch{return Response.json({error:'AUTH_STORAGE_UNAVAILABLE'},{status:503});}
 return new Response(null,{status:303,headers:{Location:origin+'/login','Set-Cookie':expiredSessionCookie(),'Cache-Control':'no-store'}});
}
