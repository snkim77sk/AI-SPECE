import {NextResponse,type NextRequest} from 'next/server';
import {guardPublicRequest} from './lib/public-auth-service';

/**
 * Hard gate on EVERY site page and API route during an explicitly approved public cutover.
 * Default (flag unset) preserves the owner-private Site behavior.
 * Public mode must be smoke-tested with fail-closed database/session auth before enabling.
 */
export async function proxy(request:NextRequest){
 const blocked=await guardPublicRequest(request);
 if(blocked)return blocked;
 return NextResponse.next();
}
export const config={matcher:['/((?!_next/static|_next/image|favicon.ico).*)']};
