import {collectionRecovery} from '../../../../lib/g2b-service';
export const dynamic='force-dynamic';
export async function GET(request:Request){
 const id=new URL(request.url).searchParams.get('id');
 if(!id||!(/^[a-f0-9-]{36}$/.test(id)))return Response.json({error:'INVALID_SESSION_ID'},{status:400});
 try{return Response.json(await collectionRecovery(id),{headers:{'Cache-Control':'no-store'}});}
 catch(e){const known=e instanceof Error&&['COLLECTION_SESSION_NOT_FOUND','COLLECTION_DIAGNOSTIC_INVALID'].includes(e.message);return Response.json({error:known?(e as Error).message:'COLLECTION_DIAGNOSTIC_UNAVAILABLE'},{status:known?409:503,headers:{'Cache-Control':'no-store'}});}
}
