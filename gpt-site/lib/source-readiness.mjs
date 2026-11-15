/** Configuration gates only, not proof of upstream quota or source acceptance.
 * The reviewed-allocation flag must be set only after verifying all callers in
 * the provider's actual account/service scope. A local950 cap is not a shared
 * reservation system and never establishes an independent1000 allowance. */
export function sourceReadinessBlock(c,provider='g2b'){
 if(provider==='g2b')return !c.hasKey?'API_KEY_NOT_CONFIGURED':!c.approved?'LIVE_APPROVAL_REQUIRED':!c.budget?'DAILY_BUDGET_NOT_CONFIRMED':!c.quotaScopeReviewed?'G2B_QUOTA_SCOPE_NOT_REVIEWED':null;
 if(provider==='lofin')return !c.hasKey?'LOFIN_KEY_NOT_CONFIGURED':!c.approved?'LOFIN_APPROVAL_REQUIRED':!c.budget?'LOFIN_BUDGET_NOT_CONFIRMED':!c.quotaScopeReviewed?'LOFIN_QUOTA_SCOPE_NOT_REVIEWED':null;
 throw Error('INVALID_SOURCE_PROVIDER');
}
