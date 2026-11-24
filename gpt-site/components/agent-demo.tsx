"use client";
import {useState} from 'react';
import {TASKS,mockAnswer} from '../lib/agent-demo.mjs';
import './agent-demo.css';
type Answer={question:string;warning:string;claims:{text:string;refs:string[]}[];limitations:string[];evidence:{id:string;title:string;at:string;text:string}[]};
const examples:Record<string,string>={diagnosis:'수집기가 왜 대기 중인가요?',notice:'제출기한과 확인할 항목을 알려주세요.',market:'시험 표본에서 어떤 정보를 알 수 있나요?'};
export default function AgentDemo(){
 const [task,setTask]=useState('diagnosis'),[question,setQuestion]=useState(examples.diagnosis),[scenario,setScenario]=useState('normal');
 const [answer,setAnswer]=useState<Answer|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(e:React.FormEvent){e.preventDefault();setBusy(true);setAnswer(null);setError('');
  try{await new Promise(resolve=>setTimeout(resolve,150));setAnswer(mockAnswer({task,question,scenario}));}
  catch(e){const code=e instanceof Error?e.message:'';setError(code==='ACCESS_DENIED'?'시험 권한이 없어 답변과 근거를 표시하지 않았습니다.':code==='MOCK_UNAVAILABLE'?'모의 응답 오류입니다. 정상 상황으로 바꿔 다시 시도하세요.':'질문을 1~500자로 입력하세요.');}
  finally{setBusy(false);}
 }
 return <main className="agent-lab"><header><div className="agent-wordmark">SINSUNG / G2B</div><span className="agent-tag">모의 AI · 개발 전용</span><h1>근거를 확인하며 묻는 업무 도우미</h1><p>시험 자료로 질문과 답변 흐름을 확인하세요. 실제 모델과 운영 자료는 연결되지 않았습니다.</p></header>
 <div className="agent-grid"><form onSubmit={submit}><h2>무엇을 확인할까요?</h2><label htmlFor="task">업무</label><select id="task" value={task} disabled={busy} onChange={e=>{setTask(e.target.value);setQuestion(examples[e.target.value]);setAnswer(null);setError('');}}>{Object.entries(TASKS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select>
 <label htmlFor="question">질문</label><textarea id="question" rows={5} maxLength={500} required value={question} disabled={busy} onChange={e=>setQuestion(e.target.value)}/><small>{question.length}/500자 · 질문은 저장하거나 외부에 전송하지 않습니다.</small>
 <label htmlFor="scenario">시험 상황</label><select id="scenario" value={scenario} disabled={busy} onChange={e=>{setScenario(e.target.value);setAnswer(null);setError('');}}><option value="normal">정상 응답</option><option value="stale">오래된 자료</option><option value="denied">권한 거부 (모의)</option><option value="error">응답 오류 (모의)</option></select>
 <button disabled={busy||!question.trim()} type="submit">{busy?'시험 답변 준비 중…':'시험 답변 보기'}</button><p className="agent-cost">모델 호출 0회 · 모델 사용료 없음</p></form>
 <section aria-label="답변" aria-live="polite" aria-busy={busy}><h2>답변과 근거</h2>{error&&<p role="alert" className="agent-error">{error}</p>}{!answer&&!error&&<p className="agent-empty">질문을 보내면 모의 답변과 근거 카드가 함께 표시됩니다.</p>}{answer&&<><p className="agent-warning">{answer.warning}</p><p className="agent-question">질문: {answer.question}</p>{answer.claims.map((claim,i)=><article key={i}><p>{claim.text}</p>{claim.refs.map(ref=><a className="agent-ref" key={ref} href={'#'+ref} onClick={()=>document.getElementById(ref)?.focus()}>근거 보기 ↗</a>)}</article>)}<h3>답변의 한계</h3><ul>{answer.limitations.map(text=><li key={text}>{text}</li>)}</ul><h3>사용한 시험 자료</h3>{answer.evidence.map(item=><section className="agent-evidence" id={item.id} key={item.id} tabIndex={-1}><strong>{item.title}</strong><time>{item.at}</time><p>{item.text}</p><span>합성 자료 · 원문 링크 없음</span></section>)}</>}</section></div>
 <footer>현재 화면은 연결 전 사용성 검증용입니다. 실제 인증·권한 및 유료 모델 연결은 별도 검증이 필요합니다.</footer></main>;
}
