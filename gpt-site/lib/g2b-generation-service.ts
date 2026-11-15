import {env} from 'cloudflare:workers';
import {config,database} from './g2b-service';
import {generationService} from './g2b-generation-runtime.mjs';
export const recoveryGeneration=()=>generationService({db:database(),configuration:()=>{const c=config();return {enabled:env.G2B_GENERATION_RECOVERY_ENABLED,generationId:env.G2B_GENERATION_RECOVERY_ID,rootId:env.G2B_GENERATION_RECOVERY_ROOT_ID,diagnosticId:env.G2B_GENERATION_RECOVERY_DIAGNOSTIC_ID,liveReady:c.liveReady,budget:c.budget,from:c.from,to:c.to,serviceKey:env.G2B_SERVICE_KEY};}});
