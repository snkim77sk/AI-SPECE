import {guardPublicRequest} from '../../../lib/public-auth-service';
import {facets} from '../../../lib/g2b-service';
export const dynamic='force-dynamic';
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return Response.json(await facets(new URL(request.url).searchParams.get('mode')||'live'),{headers:{'Cache-Control':'no-store'}});}catch{return Response.json({error:'FACETS_UNAVAILABLE'},{status:503});}}
