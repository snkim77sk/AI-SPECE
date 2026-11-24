import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as matching from '../lib/matching-ui.mjs';
import * as review from '../lib/review-preview.mjs';
import * as navigation from '../lib/delivery-navigation.mjs';

// Exercise real page handlers and async continuations without opening a browser,
// contacting an API, or changing stored data. Role changes reuse the same page.
function harness(page,{role='member',preview=false}={}){
 const slots=[],effects=[],requests=[],downloads=[],listeners=new Map();
 let index=0,dirty=true,tree,url=new URL('https://example.test/'+page+'?mode=live');
 function viewerHref(target){
  if(!preview||!target.startsWith('/')||target.startsWith('//'))return target;
  const next=new URL(target,url);next.searchParams.set('viewer','member');
  return next.pathname+next.search+next.hash;
 }
 const hooks={
  useState(initial){const n=index++;if(!(n in slots))slots[n]=typeof initial==='function'?initial():initial;return [slots[n],value=>{slots[n]=typeof value==='function'?value(slots[n]):value;dirty=true;}];},
  useRef(initial){const n=index++;if(!(n in slots))slots[n]={current:initial};return slots[n];},
  useMemo(fn,deps){const n=index++,previous=slots[n];if(!previous||deps.some((value,i)=>!Object.is(value,previous.deps[i])))slots[n]={deps,value:fn()};return slots[n].value;},
  useEffect(fn,deps){const n=index++,previous=slots[n];if(!previous||deps.some((value,i)=>!Object.is(value,previous.deps[i]))){const next={effect:true,deps,cleanup:null};slots[n]=next;effects.push(()=>{previous?.cleanup?.();next.cleanup=fn();});}},
 };
 const jsx=(type,props)=>typeof type==='function'?type(props):({type,props});
 const window={get location(){return {href:url.href,search:url.search};},history:{state:null,replaceState(state,_,next){this.state=state;url=new URL(next,url);}},dispatchEvent(event){listeners.get(event.type)?.();},addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(type){listeners.delete(type);}};
 const fetch=(target,options={})=>new Promise((resolve,reject)=>requests.push({target,options,resolve:data=>resolve({ok:true,status:200,json:async()=>data}),reject}));
 const imports={react:hooks,'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},'next/navigation':{useSearchParams:()=>url.searchParams},
  '../../lib/matching-ui.mjs':matching,'../../lib/review-preview.mjs':review,'../../lib/delivery-navigation.mjs':navigation,
  '../../components/source-availability':{default:()=>null},
  '../../components/viewer-access':{useViewerAccess:()=>({canRead:role==='admin'||role==='member',canManage:role==='admin'&&!preview,isAdministrator:role==='admin',preview,loading:role==='loading',publicMembership:true,error:'',href:viewerHref})}};
 const source=readFileSync(new URL('../app/'+page+'/page.tsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};
 vm.runInNewContext(code,{exports,require:name=>{assert.ok(name in imports,'unexpected import '+name);return imports[name];},fetch,window,URL,URLSearchParams,AbortSignal,Event,crypto,Blob,
  document:{createElement:()=>({click(){downloads.push(this.download);}})}});
 function render(){index=0;dirty=false;tree=exports.default();while(effects.length)effects.shift()();}
 async function flush(){for(let n=0;n<24;n++){await Promise.resolve();if(dirty)render();}}
 function all(node,out=[]){if(Array.isArray(node)){node.forEach(item=>all(item,out));return out;}if(node&&typeof node==='object'){out.push(node);all(node.props?.children,out);}return out;}
 function text(node){return typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):'';}
 render();
 return {requests,downloads,flush,all:()=>all(tree),text:()=>text(tree),url:()=>url,
  control:label=>all(tree).find(node=>node.props?.['aria-label']===label),
  button:label=>all(tree).find(node=>node.type==='button'&&text(node)===label),
  setAccess(next){role=next.role??role;preview=next.preview??preview;dirty=true;},
  dispose(){for(const slot of slots)if(slot?.effect)slot.cleanup?.();}};
}
const postCount=h=>h.requests.filter(request=>request.options.method==='POST').length;
const item=(id='stored',state='complete')=>({id,mode:'live',state,phase:'DONE',from_day:'2026-01-01',to_day:'2026-01-02',budget_count:1,request_count:1,candidate_count:1,match_count:1,excluded_count:0,completed_groups:1,indexed_projects:1,indexed_requests:1,examined:1,source_limit:200,institution_run_id:'source',options:{service:'AIDFA',year:'2027',history:'2',category:'',region:'',org:'',q:''}});
const cases=[
 {page:'matching',create:'새 매칭 계획',step:'연속 분석',cancel:'계획 취소',stop:'분석 중단',read:'결과 조회',input:'매칭 점수 분류',
  workspace:run=>({job:run,jobs:[run],window:{from:'2026-01-01',to:'2026-11-01'}}),
  result:run=>({job:run,rows:[{shopping_source_key:'row',budget_project:'SAVED_ROW',evidence:[]}],next:null})},
 {page:'institution-patterns',create:'새 기관 분석',step:'연속 분석',cancel:'계획 취소',stop:'분석 중단',read:'첫 페이지 조회',input:'기관 근거 분류',
  workspace:run=>({run,runs:[run],sources:[{id:'source',usable:true,from_day:'2026-01-01',to_day:'2026-01-02'}]}),
  result:run=>({run,rows:[{org_name:'SAVED_ROW',region_name:'서울특별시',category:'LIGHTING',scope:{fiscal_years:['2026']}}],next:null})},
 {page:'future-ranking',create:'새 정렬 만들기',step:'계속 정렬',cancel:'작업 취소',stop:'정렬 중단',read:'첫25건 조회',
  workspace:run=>({run,runs:[run],sources:[{id:'source',usable:true,completed_groups:1}]}),
  result:run=>({run,rows:[{source_key:'row',org_name:'SAVED_ROW'}],next:null})},
];
async function populate(h,c,run=item()){
 h.requests[0].resolve(c.workspace(run));await h.flush();
 if(c.page==='matching'){h.requests[1].resolve(c.result(run));await h.flush();}
}
for(const c of cases){
 test(c.page+': approved member reads saved results and selection without operation controls',async()=>{
  const h=harness(c.page);
  try{
   await populate(h,c);
   for(const label of [c.create,c.step,c.cancel,c.stop,'재개 준비'])assert.equal(h.button(label),undefined,label);
   assert.ok(h.button(c.read));if(c.input)assert.ok(h.control(c.input));
   if(c.page!=='matching'){
    if(c.page==='institution-patterns')h.all().find(node=>node.type==='form').props.onSubmit({preventDefault(){}});
    else h.button(c.read).props.onClick();
    await h.flush();h.requests.at(-1).resolve(c.result(item()));await h.flush();
   }
   assert.match(h.text(),/SAVED_ROW/);
   const history=h.all().find(node=>node.type==='section'&&node.props.className==='panel history');
   const saved=history.props.children.flat().find(node=>Array.isArray(node))?.[0]||history.props.children[1][0];
   saved.props.onClick();await h.flush();
   assert.equal(new URL(h.requests.at(-1).target,'https://example.test').searchParams.get('id'),'stored');
   assert.equal(postCount(h),0);
   assert.ok(h.requests.every(request=>!request.target.includes('viewer=member')));
  }finally{h.dispose();}
 });
 test(c.page+': owner member preview carries its read-only scope through reads and links',async()=>{
  const h=harness(c.page,{role:'admin',preview:true});
  try{
   await populate(h,c);
   assert.equal(h.button(c.create),undefined);
   assert.ok(h.requests.length>0);
   assert.ok(h.requests.every(request=>new URL(request.target,'https://example.test').searchParams.get('viewer')==='member'));
   const links=h.all().filter(node=>node.type==='a'&&node.props.href.startsWith('/'));
   assert.ok(links.length>0);assert.ok(links.every(node=>new URL(node.props.href,'https://example.test').searchParams.get('viewer')==='member'));
   const history=h.all().find(node=>node.type==='section'&&node.props.className==='panel history');
   history.props.children[1][0].props.onClick();await h.flush();
   assert.equal(h.url().searchParams.get('viewer'),'member');
   assert.equal(new URL(h.requests.at(-1).target,'https://example.test').searchParams.get('viewer'),'member');
   assert.equal(postCount(h),0);
  }finally{h.dispose();}
 });
 test(c.page+': changing to member stops a running continuation and rejects retained admin handlers',async()=>{
  const h=harness(c.page,{role:'admin'});
  try{
   await populate(h,c,item('stored','ready'));
   const oldHandlers=[c.create,c.cancel,'재개 준비',c.stop].map(label=>h.button(label).props.onClick);
   h.button(c.step).props.onClick();await h.flush();
   const pending=h.requests.find(request=>request.options.method==='POST');
   assert.ok(pending);assert.equal(JSON.parse(pending.options.body).action,'step');
   h.setAccess({preview:true});await h.flush();
   pending.resolve(c.workspace(item('stored','ready')));await h.flush();
   for(const handler of oldHandlers)handler();await h.flush();
   assert.equal(postCount(h),1,'no subsequent step, pause, or retained create/resume/cancel');
   assert.equal(h.button(c.create),undefined);
  }finally{h.dispose();}
 });
}

for(const page of [...cases.map(c=>c.page),'contract-review','future-evidence']){
 test(page+': unresolved or unapproved viewer never starts a data query',async()=>{
  const h=harness(page,{role:'loading'});
  try{await h.flush();assert.equal(h.requests.length,0);h.setAccess({role:'pending'});await h.flush();assert.equal(h.requests.length,0);assert.match(h.text(),/조회 권한/);}
  finally{h.dispose();}
 });
}

test('contract review: member keeps contract/budget searches while admin status and file input are absent',async()=>{
 const h=harness('contract-review');
 try{
  await h.flush();assert.equal(h.requests.length,1);assert.match(h.requests[0].target,/^\/api\/deliveries\?/);
  assert.equal(h.control('계약 또는 예산 CSV JSON 파일'),undefined);
  assert.ok(!h.all().some(node=>node.type==='a'&&node.props.href.includes('collection-monitor')));
  h.requests[0].resolve({rows:[{request_no:'SAVED_CONTRACT',agency:'저장 기관'}],next:null});await h.flush();
  assert.match(h.text(),/SAVED_CONTRACT/);
  h.all().find(node=>node.type==='select'&&node.props.value==='contract').props.onChange({target:{value:'budget'}});await h.flush();
  h.control('통합 검색어').props.onChange({target:{value:'조명'}});await h.flush();
  h.button('Sites 저장자료 조회').props.onClick();await h.flush();
  const request=h.requests.at(-1),url=new URL(request.target,'https://example.test');
  assert.equal(url.pathname,'/api/budget');assert.equal(url.searchParams.get('q'),'조명');
  request.resolve({rows:[{project:'SAVED_BUDGET'}],next:null});await h.flush();
  assert.match(h.text(),/SAVED_BUDGET/);
  assert.ok(h.requests.every(request=>!/(server-sync|automation)/.test(request.target)));
 }finally{h.dispose();}
});

test('contract review: preview carries viewer scope through queries and internal links',async()=>{
 const h=harness('contract-review',{role:'admin',preview:true});
 try{
  await h.flush();assert.equal(h.requests.length,1);
  assert.equal(new URL(h.requests[0].target,'https://example.test').searchParams.get('viewer'),'member');
  const links=h.all().filter(node=>node.type==='a'&&node.props.href.startsWith('/'));
  assert.ok(links.length>0);assert.ok(links.every(node=>new URL(node.props.href,'https://example.test').searchParams.get('viewer')==='member'));
  assert.equal(h.control('계약 또는 예산 CSV JSON 파일'),undefined);
 }finally{h.dispose();}
});

test('contract review: administrator retains local review and CSV export but stale file handlers stop after downgrade',async()=>{
 const h=harness('contract-review',{role:'admin'});
 try{
  await h.flush();assert.equal(h.requests.length,3);
  assert.ok(h.requests.some(request=>request.target==='/api/server-sync'));
  assert.ok(h.requests.some(request=>request.target==='/api/automation'));
  const oldFile=h.control('계약 또는 예산 CSV JSON 파일').props.onChange;
  oldFile({target:{files:[{name:'contract.json',size:100,text:async()=>JSON.stringify([{request_no:'LOCAL_CONTRACT',agency:'한국토지주택공사'}])}]}});await h.flush();
  assert.match(h.text(),/LOCAL_CONTRACT/);const oldDownload=h.button('현재 필터 CSV 내려받기').props.onClick;
  oldDownload();assert.deepEqual(h.downloads,['G2B_contract_review.csv']);
  let finish;
  oldFile({target:{files:[{name:'late.json',size:100,text:()=>new Promise(resolve=>{finish=resolve;})}]}});await h.flush();
  h.setAccess({role:'member'});await h.flush();
  finish(JSON.stringify([{request_no:'LATE_PRIVATE_FILE'}]));await h.flush();
  let fileRead=false;
  oldFile({target:{files:[{name:'not-read.json',size:100,text:async()=>{fileRead=true;return '[]';}}]}});
  oldDownload();await h.flush();
  assert.equal(fileRead,false);assert.equal(h.downloads.length,1);
  assert.doesNotMatch(h.text(),/LOCAL_CONTRACT|LATE_PRIVATE_FILE|신규 계약·예산 파일/);
  h.requests.find(request=>request.target==='/api/server-sync').resolve({sources:[{dataset:'PRIVATE_STATUS',pages:1}]});
  h.requests.find(request=>request.target==='/api/automation').resolve({});await h.flush();
  assert.doesNotMatch(h.text(),/PRIVATE_STATUS|자료 출처와 갱신 상태/);
 }finally{h.dispose();}
});

for(const preview of [false,true]){
 test('future evidence: saved-data search preserves filters and '+(preview?'member preview':'member access'),async()=>{
  const h=harness('future-evidence',{role:preview?'admin':'member',preview});
  try{
   await h.flush();assert.equal(h.requests.length,1);
   h.requests[0].resolve({runs:[{id:'stored-evidence',usable:true,completed_groups:1}]});await h.flush();
   for(const [label,value] of [['미래 근거 분류','POLE'],['미래 근거 지역','인천광역시'],['미래 근거 검색어','조명']]){
    h.control(label).props.onChange({target:{value}});await h.flush();
   }
   h.all().find(node=>node.type==='form').props.onSubmit({preventDefault(){}});await h.flush();
   const request=h.requests.at(-1),url=new URL(request.target,'https://example.test');
   assert.equal(url.pathname,'/api/future-evidence');assert.equal(url.searchParams.get('action'),'results');
   assert.equal(url.searchParams.get('id'),'stored-evidence');assert.equal(url.searchParams.get('category'),'POLE');
   assert.equal(url.searchParams.get('region'),'인천광역시');assert.equal(url.searchParams.get('q'),'조명');
   for(const target of [...h.requests.map(request=>request.target),h.url().href,...h.all().filter(node=>node.type==='a'&&node.props.href.startsWith('/')).map(node=>node.props.href)]){
    assert.equal(new URL(target,'https://example.test').searchParams.get('viewer'),preview?'member':null);
   }
   request.resolve({selectedYears:['2026'],source:{from:'2026-01-01',to:'2026-02-01'},rows:[{source_key:'row',org_name:'SAVED_FUTURE_EVIDENCE',historical_evidence_years:['2026']}],next:null});await h.flush();
   assert.match(h.text(),/SAVED_FUTURE_EVIDENCE/);assert.equal(postCount(h),0);
   const oldSearch=h.all().find(node=>node.type==='form').props.onSubmit;
   const before=h.requests.length;h.setAccess({role:'pending',preview:false});await h.flush();
   oldSearch({preventDefault(){}});await h.flush();
   assert.equal(h.requests.length,before);assert.doesNotMatch(h.text(),/SAVED_FUTURE_EVIDENCE/);
  }finally{h.dispose();}
 });
}
