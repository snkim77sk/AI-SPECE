/** Owner-only G2B progress and change-request helpers. No GitHub credentials or mutations. */
export const PROJECT_REPOSITORY='https://github.com/snkim77sk/AI-SPECE';
// Reviewed upstream baseline, not the SHA of a subsequent Sites deployment.
export const SOURCE_VERSION='5.2.21';
export const CODE_SNAPSHOT_SHA='daf8abfb0deeae77f111662e34672c7f2cb4bc9e';
export const STAGES=Object.freeze([
 {id:'source',name:'Windows 자동수집·동기화',status:'unverified',detail:'현재 수신 영수증을 확인해야 표시됩니다. 수신 정보가 없거나 조회에 실패하면 미확인으로 유지합니다.',href:'/collection-monitor'},
 {id:'review',name:'계약·예산 조회',status:'operator-verified',detail:'기존 비공개 Sites 계약·예산 조회 확인. 신규일·조회범위 완전성은 별도',href:'/contract-review'},
 {id:'frontend',name:'GitHub 공개인증 개발',status:'code-verified',detail:'소셜 로그인·관리자 승인의 GitHub 코드/CI 검증. 실제 제공자 인증은 별도',href:PROJECT_REPOSITORY+'/pull/378'},
 {id:'deploy',name:'GPT Sites 실제 실행 버전',status:'unverified',detail:'실행 서버의 버전 응답을 조회합니다. GitHub 기준 커밋만으로 실제 배포 완료를 표시하지 않습니다.',href:null},
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
