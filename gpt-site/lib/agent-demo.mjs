/** Offline fixtures only. No model, transport, DB, or executable tool access. */
export const TASKS = {diagnosis:'수집 진단',notice:'용역공고 분석',market:'시장정보 분석'};
const DATA = {
 diagnosis:[{id:'demo-collection',title:'시험 수집 상태',at:'2026-11-17 09:00 KST',text:'시험 수집기: 100회 한도 중 100회 사용. 다음 기준시간까지 대기.',fact:'시험 수집기는 설정된 호출 한도를 사용해 대기 중입니다.'}],
 notice:[{id:'demo-notice',title:'시험 용역공고 DEMO-001',at:'2026-11-17 09:00 KST',text:'가상기관 조명시설 점검 용역. 제출기한 2026-11-20 15:00 KST. 참가자격 자료는 없음.',fact:'시험 공고의 제출기한은 2026년 11월 20일 15시입니다. 참가자격은 제공 자료로 판단할 수 없습니다.'}],
 market:[{id:'demo-market',title:'시험 납품 집계',at:'2026-11-17 09:00 KST',text:'가상기관 A·B, 시험 납품 12건, 합계 120,000,000원. 2026년 10월 부분 표본.',fact:'시험 표본은 12건·1억 2천만 원입니다. 일부 기관 표본이므로 전체 시장 규모로 해석할 수 없습니다.'}]
};
export function validateCitations(answer) {
 const ids=new Set(answer.evidence.map(x=>x.id));
 if(!answer.claims.every(x=>Array.isArray(x.refs)&&x.refs.length&&x.refs.every(id=>ids.has(id))))throw Error('UNKNOWN_CITATION');
 return answer;
}
export function mockAnswer({task,question,scenario='normal'}) {
 if(!Object.hasOwn(TASKS,task))throw Error('INVALID_TASK');
 if(typeof question!=='string'||!question.trim()||question.length>500)throw Error('INVALID_QUESTION');
 if(!['normal','stale','denied','error'].includes(scenario))throw Error('INVALID_SCENARIO');
 if(scenario==='denied')throw Error('ACCESS_DENIED');
 if(scenario==='error')throw Error('MOCK_UNAVAILABLE');
 const evidence=DATA[task].map(x=>({...x}));
 const warning=scenario==='stale'?'자료가 오래된 상황을 재현했습니다. 현재 상태로 단정하지 마세요.':'고정된 시험 자료이며 실제 운영 현황이 아닙니다.';
 return validateCitations({provider:'mock',modelCalls:0,question:question.trim(),claims:[{text:evidence[0].fact,refs:[evidence[0].id]}],evidence,warning,
  limitations:['모의 모델은 선택한 업무의 고정 자료만 설명합니다. 자유로운 질문 해석은 아직 지원하지 않습니다.','실제 자료 조회·모델 연결·운영 조작은 수행하지 않았습니다.'],factsVerified:false});
}
