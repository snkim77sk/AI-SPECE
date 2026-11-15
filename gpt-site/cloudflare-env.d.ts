declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    G2B_SERVICE_KEY?: string;
    G2B_LIVE_ENABLED?: string;
    G2B_QUOTA_SCOPE_REVIEWED?: string;
    G2B_DAILY_BUDGET?: string;
    G2B_AUTOCOLLECT_ENABLED?: string;
    G2B_DIAGNOSTIC_PROBE_ENABLED?: string;
    G2B_GENERATION_RECOVERY_ENABLED?: string;
    G2B_GENERATION_RECOVERY_ID?: string;
    G2B_GENERATION_RECOVERY_ROOT_ID?: string;
    G2B_GENERATION_RECOVERY_DIAGNOSTIC_ID?: string;
    G2B_DIAGNOSTIC_V2_ENABLED?: string;
    G2B_DIAGNOSTIC_V2_REQUEST_ID?: string;
    G2B_DIAGNOSTIC_V2_SESSION_ID?: string;
    G2B_DIAGNOSTIC_V2_PRIOR_PROBE_ID?: string;
    LOFIN_API_KEY?: string;
    LOFIN_LIVE_ENABLED?: string;
    LOFIN_DAILY_BUDGET?: string;
    LOFIN_QUOTA_SCOPE_REVIEWED?: string;
    LOFIN_AUTOCOLLECT_ENABLED?: string;
    BUCKET?: R2Bucket;
  }
}
