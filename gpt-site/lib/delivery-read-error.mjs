/** Only safe diagnostics from the delivery read endpoint may reach the UI.
 * Never echo SQL, query parameters, response bodies or credentials. */
/** @param {number} status @param {any} payload @param {any} cause */
export function deliveryReadNotice(status=0, payload=null, cause=null) {
 const raw=payload&&typeof payload==='object'?payload:{};
 const code=typeof raw.error==='string'&&/^[A-Z0-9_]{3,64}$/.test(raw.error)?raw.error:null;
 const incident=typeof raw.incident==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw.incident)?raw.incident:null;
 let reason;
 if(code==='INVALID_DATE_RANGE'||status===400)reason='조회기간이 허용 범위를 벗어났습니다. 시작일·종료일을 확인해 주세요.';
 else if(['DATABASE_BUSY','DATABASE_OVERLOADED','DATABASE_TIMEOUT','DATABASE_CONNECTION','DATABASE_RESOURCE_LIMIT','DATABASE_STATEMENT_LIMIT','DATABASE_SCHEMA','DATABASE_D1_ERROR','DATABASE_UNKNOWN'].includes(code))
   reason='조회 저장소에서 오류가 발생했습니다. 저장된 자료가 없다는 뜻은 아닙니다.';
 else if(cause?.name==='TimeoutError'||cause?.name==='AbortError')
   reason='조회 요청이 15초 안에 완료되지 않았습니다. 수집 중단과는 별개이며 재조회가 필요합니다.';
 else if(cause?.message==='INVALID_READ_RESPONSE')
   reason='검색 API 응답 형식을 확인할 수 없습니다. 화면 결과를 확정하지 않았습니다.';
 else if(status===0)
   reason='검색 API와 통신하지 못했습니다. 저장자료가 없다는 뜻은 아닙니다.';
 else reason='저장자료 조회를 완료하지 못했습니다. 자료가 없다고 판단하지 마세요.';
 const diagnosis=code?'오류코드 '+code:status?'HTTP '+status:'';
 return reason+(diagnosis?' ('+diagnosis+(incident?' · 진단번호 '+incident:'')+')':incident?' (진단번호 '+incident+')':'');
}
