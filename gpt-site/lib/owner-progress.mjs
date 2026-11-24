/** Owner-only G2B progress and change-request helpers. No GitHub credentials or mutations. */
export const PROJECT_REPOSITORY='https://github.com/snkim77sk/AI-SPECE';
export const SOURCE_VERSION='5.2.20';
export const CODE_SNAPSHOT_SHA='71457551c8ee940b826bd7b71b19a2b2af0007bf';
export const STAGES=Object.freeze([
 {id:'source',name:'Windows 자동수집·동기화',status:'operator-verified',detail:'운영 수집 및 수신은 Codex의 기존 증빙으로 확인. 마지막 최신 수신은 이 화면에서 읽기 조회',href:'/collection-monitor'},
 {id:'review',name:'계약·예산 조회',status:'operator-verified',detail:'기존 비공개 Sites 계약·예산 조회 확인. 신규일·조회범위 완전성은 별도',href:'/contract-review'},
 {id:'frontend',name:'GitHub 공개인증 개발',status:'code-verified',detail:'소셜 로그인·관리자 승인의 GitHub 코드/CI 검증. 실제 제공자 인증은 별도',href:PROJECT_REPOSITORY+'/pull/378'},
 {id:'deploy',name:'GPT Sites 최신 운영 배포',status:'unverified',detail:'운영 사이트는 마지막 확인에서 비공개 v5.2.18 배포 63. 최신 배포 여부 재검증 필요',href:null},
 {id:'oauth',name:'실제 소셜 계정·관리자 인증',status:'blocked',detail:'Kakao/Daum 통합·Naver·Google 실키·콜백·초기 관리자 검증 필요',href:null},
 {id:'publish',name:'외부 공개 전환',status:'blocked',detail:'실제 인증·안전성·개인정보·롤백 시험 전까지 비공개 유지',href:null},
]);
const types=Object.freeze({bug:'오류 수정',ui:'화면 개선',data:'신규자료/수집',auth:'로그인/승인',request:'기능 추가'});
export const FEEDBACK_TYPES=Object.freeze(Object.entries(types).map(([value,label])=>({value,label})));
export function validateFeedback(input){
 const type=String(input?.type??''),title=String(input?.title??'').trim(),detail=String(input?.detail??'').trim();
 if(!Object.hasOwn(types,type))return {ok:false,error:'요청 유형을 선택해 주세요.'};
 if(title.length<4||title.length>100)return {ok:false,error:'요청 제목은 4~100자로 입력해 주세요.'};
 if(detail.length<12||detail.length>2400)return {ok:false,error:'수정 내용은 12~2400자로 입력해 주세요.'};
 return {ok:true,value:{type,title,detail}};
}
export function feedbackIssueUrl(input){
 const result=validateFeedback(input);
 if(!result.ok)throw Error(result.error);
 const {type,title,detail}=result.value;
 const issueTitle='[G2B '+types[type]+'] '+title;
 const body=['## 수정·보완 요청','- 유형: '+types[type],'- 요청 화면: G2B 개발진행 /owner-progress','','### 요청 내용',detail,'','### 검증 요청','- 기존 운영 DB·RAW·체크포인트·예약작업 및 NO1 보존','- 서버·Sites 실제 반영 여부와 검증 결과를 PR에서 보고','- 공개 전환은 운영 보안 검증 전까지 금지','','※ 비밀번호·API 키·개인정보를 GitHub 이슈에 포함하지 마세요.'].join('\n');
 const url=new URL(PROJECT_REPOSITORY+'/issues/new');
 url.searchParams.set('title',issueTitle);url.searchParams.set('body',body);
 return url.toString();
}
