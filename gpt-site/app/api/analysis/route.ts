import {guardPublicRequest} from '../../../lib/public-auth-service';
import {analysisRows} from '../../../lib/g2b-service';
export const dynamic='force-dynamic';
export async function GET(request:Request){const __publicBlock=await guardPublicRequest(request);if(__publicBlock)return __publicBlock;try{return Response.json(await analysisRows(new URL(request.url).searchParams),{headers:{'Cache-Control':'no-store'}});}catch(e){return Response.json({error:/^(INVALID_DATE_RANGE|AMOUNT_OUT_OF_RANGE|DELIVERY_SUMMARY_(INVALID_REVIEW_LIMIT|INVALID_CURSOR|SOURCE_CHANGED_RETRY))$/.test((e as Error).message)?(e as Error).message:'ANALYSIS_UNAVAILABLE'},{status:409});}}
