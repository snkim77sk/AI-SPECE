import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {createRequire,registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import ts from 'typescript';

registerHooks({resolve(spec,ctx,next){
  if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
    const candidate=new URL(spec+'.ts',ctx.parentURL);
    if(existsSync(fileURLToPath(candidate)))return next(candidate.href,ctx);
  }
  return next(spec,ctx);
}});
const {viewerHref}=await import('../lib/viewer-access.ts');
const access=(patch={})=>{
  const state={canRead:true,canManage:false,isAdministrator:false,loading:false,preview:false,publicMembership:true,error:'',...patch};
  return {...state,href:target=>viewerHref(target,state.preview)};
};
const administrator=()=>access({canManage:true,isAdministrator:true});
const preview=()=>access({isAdministrator:true,preview:true});
const closed=()=>access({canRead:false,loading:true});

// Execute actual TSX and its event handlers with deterministic hooks. Fetch never
// reaches a server, database or provider; each response is supplied by a test.
function harness(file,{role=access(),path='/dashboard',props={},entry='default',now='2026-11-24T16:00:00Z'}={}){
  const slots=[],effects=[],requests=[],listeners=new Map();
  let index=0,dirty=true,tree,currentAccess=role,url=new URL(path,'https://g2b.test');
  const changed=(previous,next)=>!previous||!next||previous.length!==next.length||next.some((value,i)=>!Object.is(value,previous[i]));
  const hooks={
    useState(initial){const n=index++;if(!(n in slots))slots[n]=typeof initial==='function'?initial():initial;return[slots[n],value=>{const next=typeof value==='function'?value(slots[n]):value;if(!Object.is(slots[n],next)){slots[n]=next;dirty=true;}}];},
    useRef(initial){const n=index++;if(!(n in slots))slots[n]={current:initial};return slots[n];},
    useEffect(fn,deps){const n=index++,previous=slots[n];if(changed(previous?.deps,deps)){const next={kind:'effect',deps,cleanup:null};slots[n]=next;effects.push(()=>{previous?.cleanup?.();next.cleanup=fn();});}},
    useMemo(fn,deps){const n=index++,previous=slots[n];if(changed(previous?.deps,deps))slots[n]={deps,value:fn()};return slots[n].value;},
    useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
  };
  const runtime={jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props}),Fragment:'fragment'};
  const window={
    get location(){return{href:url.href,pathname:url.pathname,search:url.search};},
    history:{state:null,replaceState(state,_,next){this.state=state;url=new URL(next,url);},pushState(state,_,next){this.state=state;url=new URL(next,url);}},
    addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);},
    removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
    dispatchEvent(event){for(const fn of listeners.get(event.type)||[])fn(event);},
  };
  const fetch=(target,options={})=>new Promise((resolve,reject)=>requests.push({target:String(target),options,resolve:data=>resolve(Response.json(data)),respond:resolve,reject}));
  const sourceUrl=new URL(file,import.meta.url),localRequire=createRequire(sourceUrl);
  const compiled=ts.transpileModule(readFileSync(sourceUrl,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,esModuleInterop:true,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const exports={};
  class FixedDate extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return new Date(now).getTime();}}
  vm.runInNewContext(compiled,{exports,require:name=>{
    if(name==='react')return hooks;
    if(name==='react/jsx-runtime')return runtime;
    if(name==='next/navigation')return{usePathname:()=>url.pathname,useSearchParams:()=>url.searchParams};
    if(name.endsWith('/viewer-access')||name==='./viewer-access')return{useViewerAccess:()=>currentAccess,ViewerAccessProvider:'viewer-provider'};
    if(name==='lucide-react')return new Proxy({},{get:(_,key)=>'icon-'+String(key)});
    if(name.endsWith('.mjs')||name.endsWith('.json'))return localRequire(name);
    return{__esModule:true,default:name.includes('server-sync-status')?'server-sync-status':'child-'+name};
  },fetch,window,document:{getElementById:()=>null},HTMLDetailsElement:class{},URL,URLSearchParams,AbortController,AbortSignal,Event,Date:FixedDate,setTimeout,clearTimeout});
  function render(){index=0;dirty=false;tree=exports[entry](props);while(effects.length)effects.shift()();}
  async function flush(){for(let n=0;n<30;n++){await Promise.resolve();if(dirty)render();}}
  function all(node,out=[]){if(Array.isArray(node)){node.forEach(item=>all(item,out));return out;}if(node&&typeof node==='object'){out.push(node);all(node.props?.children,out);}return out;}
  function text(node){return typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):'';}
  render();
  return{requests,flush,text:()=>text(tree),all:()=>all(tree),url:()=>url,
    button:label=>all(tree).find(node=>node.type==='button'&&text(node)===label),
    control:label=>all(tree).find(node=>node.props?.['aria-label']===label),
    links:()=>all(tree).filter(node=>node.type==='a'),
    setAccess(next){currentAccess=next;dirty=true;},
    dispose(){for(const slot of slots)if(slot?.kind==='effect')slot.cleanup?.();},
  };
}
const paths=h=>h.requests.map(request=>new URL(request.target,'https://g2b.test'));
const writes=h=>h.requests.filter(request=>request.options.method==='POST');
const dashboard={mode:'live',delivery:{latest_rows:'7',stored_source_rows:'11',active_source_rows:'9',inactive_source_rows:'2'},budgets:[{service:'QWGJK',fiscal_year:2026,target_rows:'3',active_rows:'4'}],readiness:{g2b:{liveReady:true,hasKey:true},lofin:{liveReady:true,hasKey:true}},storage:{physicalRetentionEnabled:false}};

test('member shell retains browsing menus and removes management menus and live operational polling',async t=>{
  const member=harness('../components/original-shell.tsx',{entry:'OriginalFrame',props:{children:'DATA_CHILD'}});t.after(()=>member.dispose());await member.flush();
  assert.match(member.text(),/DATA_CHILD|자료 조회 전용/);
  const hrefs=member.links().map(node=>node.props.href||'');
  for(const path of ['/shopping','/budget','/smartled','/vendors'])assert.ok(hrefs.some(href=>href.startsWith(path)));
  for(const path of ['/settings','/collection-monitor','/owner-progress','/admin/members'])assert.ok(!hrefs.some(href=>href.startsWith(path)));
  assert.ok(!member.all().some(node=>node.type==='server-sync-status'));
  const admin=harness('../components/original-shell.tsx',{role:administrator(),entry:'OriginalFrame',props:{children:'DATA_CHILD'}});t.after(()=>admin.dispose());await admin.flush();
  for(const path of ['/settings','/collection-monitor','/owner-progress','/admin/members'])assert.ok(admin.links().some(node=>node.props.href?.startsWith(path)));
  assert.ok(admin.all().some(node=>node.type==='server-sync-status'));
  assert.ok(admin.links().some(node=>node.props.href==='/dashboard?viewer=member'));
});

test('unknown membership does not render data children; fixture lab mounts neither navigation nor polling',async t=>{
  const h=harness('../components/original-shell.tsx',{role:closed(),entry:'OriginalFrame',props:{children:'PRIVATE_DATA_SENTINEL'}});t.after(()=>h.dispose());await h.flush();
  assert.doesNotMatch(h.text(),/PRIVATE_DATA_SENTINEL|대시보드/);
  assert.ok(!h.all().some(node=>node.type==='nav'||node.type==='server-sync-status'));
  const lab=harness('../components/original-shell.tsx',{role:closed(),path:'/agent-lab',entry:'OriginalFrame',props:{children:'FIXTURE_LAB'}});t.after(()=>lab.dispose());await lab.flush();
  assert.equal(lab.text(),'FIXTURE_LAB');assert.equal(lab.requests.length,0);
});

test('preview shell preserves its restriction on navigation and has one explicit exit',async t=>{
  const h=harness('../components/original-shell.tsx',{role:preview(),path:'/shopping?view=history&viewer=member',entry:'OriginalFrame',props:{children:'DATA'}});t.after(()=>h.dispose());await h.flush();
  const links=h.links().filter(node=>node.props.href?.startsWith('/'));
  assert.equal(links.filter(node=>node.props.href==='/dashboard').length,1);
  for(const path of ['/owner-progress','/admin/members','/settings','/collection-monitor'])assert.ok(!links.some(node=>node.props.href.startsWith(path)));
  for(const link of links.filter(node=>node.props.href!=='/dashboard'))assert.equal(new URL(link.props.href,'https://g2b.test').searchParams.get('viewer'),'member');
  assert.ok(!h.all().some(node=>node.type==='server-sync-status'));
});

test('dashboard preview requests a restricted snapshot and displays only public business metrics',async t=>{
  const h=harness('../app/dashboard/page.tsx',{role:preview(),path:'/dashboard?viewer=member'});t.after(()=>h.dispose());await h.flush();
  assert.equal(h.requests.length,1);assert.equal(paths(h)[0].searchParams.get('viewer'),'member');
  h.requests[0].resolve(dashboard);await h.flush();
  assert.match(h.text(),/현재 대상 납품품목|양수 잔액 예산 후보/);
  assert.doesNotMatch(h.text(),/화면 코드 버전|배포 HEAD|저장소 조회|연결·저장·메모리 상태|수집 준비상태/);
  assert.ok(!h.links().some(node=>/settings|collection-monitor|storage-inspection/.test(node.props.href||'')));
  const denied=harness('../app/dashboard/page.tsx',{role:closed()});t.after(()=>denied.dispose());await denied.flush();assert.equal(denied.requests.length,0);
});

test('delivery preview reads saved data without loading collection settings and keeps history filters on requests and CSV',async t=>{
  const h=harness('../components/delivery-workspace.tsx',{role:preview(),path:'/shopping?view=history&viewer=member&category=POLE'});t.after(()=>h.dispose());await h.flush();
  assert.ok(paths(h).some(url=>url.pathname==='/api/deliveries'));
  assert.ok(paths(h).every(url=>url.pathname!=='/api/collection'&&url.searchParams.get('viewer')==='member'));
  const delivery=h.requests.find(request=>request.target.startsWith('/api/deliveries?'));
  const selected=new URL(delivery.target,'https://g2b.test').searchParams;
  assert.equal(selected.get('view'),'history');
  assert.match(selected.get('from'),/^20\d{2}-\d{2}-\d{2}$/);assert.match(selected.get('to'),/^20\d{2}-\d{2}-\d{2}$/);
  assert.equal(new Date(selected.get('to'))-new Date(selected.get('from')),6*86400000);
  assert.equal(h.control('조회 시작일').props.value,selected.get('from'));assert.equal(h.control('조회 종료일').props.value,selected.get('to'));
  delivery.resolve({rows:[],next:null});await h.flush();
  const csv=h.links().find(node=>node.props.href?.startsWith('/api/deliveries?'));
  const url=new URL(csv.props.href,'https://g2b.test');assert.equal(url.searchParams.get('format'),'csv');assert.equal(url.searchParams.get('view'),'history');assert.equal(url.searchParams.get('viewer'),'member');
  assert.equal(h.url().searchParams.get('viewer'),'member');assert.equal(h.url().searchParams.get('view'),'history');
  assert.doesNotMatch(h.text(),/새 계획 만들기|연속 처리|수집 관리/);assert.equal(writes(h).length,0);
});

test('member legacy collection and settings bookmarks cannot mount management controls or call their APIs',async t=>{
  for(const path of ['/?tab=collect','/?tab=scope','/settings?section=collection']){
    const h=harness('../components/delivery-workspace.tsx',{path});t.after(()=>h.dispose());await h.flush();
    assert.match(h.text(),/자료 조회 전용 화면/);assert.ok(!paths(h).some(url=>url.pathname==='/api/collection'||url.pathname==='/api/dashboard'));
    assert.equal(h.button('새 계획 만들기'),undefined);assert.equal(writes(h).length,0);
  }
});

test('delivery management handlers captured before a role change cannot submit writes',async t=>{
  const h=harness('../components/delivery-workspace.tsx',{role:administrator(),path:'/settings?section=collection',props:{initialTab:'collect'}});t.after(()=>h.dispose());await h.flush();
  const create=h.button('새 계획 만들기');assert.ok(create);
  h.setAccess(access());await h.flush();const before=h.requests.length;
  await create.props.onClick();await h.flush();assert.equal(h.requests.length,before);assert.equal(writes(h).length,0);
});

test('delivery administrator may start a page but a role change stops continuation after the in-flight response',async t=>{
  const h=harness('../components/delivery-workspace.tsx',{role:administrator(),path:'/settings?section=collection',props:{initialTab:'collect'}});t.after(()=>h.dispose());await h.flush();
  const workspace={config:{from:'2026-01-01',to:'2026-11-24',liveReady:true},sessions:[],receipts:[],session:{id:'PLAN',mode:'live',state:'ready',from_day:'2026-11-24',to_day:'2026-11-24',completed_days:0,page:1}};
  h.requests.find(request=>request.target.startsWith('/api/collection?')).resolve(workspace);await h.flush();
  const pending=h.button('연속 처리').props.onClick();await h.flush();
  assert.equal(writes(h).length,1);assert.equal(JSON.parse(writes(h)[0].options.body).action,'step');
  h.setAccess(access());await h.flush();
  writes(h)[0].resolve({...workspace,session:{...workspace.session,page:2}});await h.flush();await pending;
  assert.equal(writes(h).length,1);assert.equal(h.button('연속 처리'),undefined);
  assert.equal(paths(h).filter(url=>url.pathname==='/api/collection').length,2);
});

test('member budget starts its read from KST defaults without an administrative workspace request',async t=>{
  const h=harness('../app/budget/page.tsx',{role:preview(),path:'/budget?section=collection&view=targets&viewer=member',now:'2026-12-31T15:30:00Z'});t.after(()=>h.dispose());await h.flush();
  assert.equal(h.requests.length,1);const request=paths(h)[0];
  assert.equal(request.pathname,'/api/budget');assert.equal(request.searchParams.get('year'),'2027');assert.equal(request.searchParams.get('view'),'targets');assert.equal(request.searchParams.get('viewer'),'member');assert.equal(request.searchParams.has('action'),false);
  assert.equal(h.url().searchParams.has('section'),false);assert.equal(h.url().searchParams.get('viewer'),'member');
  assert.doesNotMatch(h.text(),/새 수집 계획|연속 수집|최근 예산 수집 계획|이력 누락1페이지 수집/);
  assert.ok(h.button('엑셀 다운로드'));assert.ok(h.button('검색결과 CSV'));
  h.requests[0].resolve({rows:[],next:null});await h.flush();
  assert.match(h.text(),/현재 조건의 세부사업 자료 없음/);assert.doesNotMatch(h.text(),/저장자료 조회 중/);
  const view=h.control('예산 보기');view.props.onChange({target:{value:'all'}});await h.flush();
  assert.equal(paths(h).at(-1).searchParams.get('view'),'all');assert.equal(writes(h).length,0);
});

test('member budget leaves its loading state after a failed read and recovers on retry without a collection workspace',async t=>{
  const h=harness('../app/budget/page.tsx',{path:'/budget'});t.after(()=>h.dispose());await h.flush();
  assert.match(h.text(),/저장자료 조회 중/);
  h.requests[0].respond(Response.json({error:'READ_TEMPORARILY_UNAVAILABLE'},{status:503}));await h.flush();
  assert.match(h.text(),/조회 미완료 · 오류를 확인하고 다시 조회해 주세요/);assert.doesNotMatch(h.text(),/저장자료 조회 중/);
  const form=h.all().find(node=>node.type==='form'&&node.props.className==='searchbar');form.props.onSubmit({preventDefault(){}});await h.flush();
  assert.match(h.text(),/저장자료 조회 중/);h.requests[1].resolve({rows:[],next:null});await h.flush();
  assert.match(h.text(),/현재 조건의 세부사업 자료 없음/);assert.doesNotMatch(h.text(),/저장자료 조회 중|READ_TEMPORARILY_UNAVAILABLE/);
  assert.ok(paths(h).every(url=>url.pathname==='/api/budget'&&!url.searchParams.has('action')));assert.equal(writes(h).length,0);
});

test('budget management callbacks are revoked even when a caller retains an old button',async t=>{
  const h=harness('../app/budget/page.tsx',{role:administrator(),path:'/budget'});t.after(()=>h.dispose());await h.flush();
  const create=h.button('새 수집 계획');assert.ok(create);assert.ok(paths(h).some(url=>url.searchParams.get('action')==='workspace'));
  h.setAccess(access());await h.flush();const before=h.requests.length;
  await create.props.onClick();await h.flush();assert.equal(h.requests.length,before);assert.equal(writes(h).length,0);assert.equal(h.button('새 수집 계획'),undefined);
});

test('budget navigator hides operation links for members and carries preview through saved-data navigation reads',async t=>{
  const props={mode:'live',service:'QWGJK',year:'2026',region:'인천광역시',orgKey:'',deptKey:'',onChange(){}};
  const h=harness('../app/budget/components/navigator.tsx',{role:preview(),path:'/budget?viewer=member',props});t.after(()=>h.dispose());await h.flush();
  assert.equal(paths(h)[0].searchParams.get('viewer'),'member');
  h.requests[0].resolve({service:'QWGJK',year:2026,coverage:{services:[],regions:[],choices:[]}});await h.flush();
  assert.ok(!h.links().some(node=>node.props.href?.includes('collection-monitor')));
  const denied=harness('../app/budget/components/navigator.tsx',{role:closed(),props});t.after(()=>denied.dispose());await denied.flush();assert.equal(denied.requests.length,0);
});

test('budget history pages retain read-only preview through filters, navigation and data reads',async t=>{
  for(const section of ['history','observations']){
    const h=harness(`../app/budget/${section}/page.tsx`,{role:preview(),path:`/budget/${section}?view=targets&viewer=member&year=2026`});t.after(()=>h.dispose());await h.flush();
    assert.equal(h.requests.length,1);assert.equal(paths(h)[0].searchParams.get('view'),'targets');assert.equal(paths(h)[0].searchParams.get('viewer'),'member');
    assert.ok(!h.links().some(node=>node.props.href?.includes('collection-monitor')));
    for(const link of h.links())assert.equal(new URL(link.props.href,'https://g2b.test').searchParams.get('viewer'),'member');
    h.requests[0].resolve({rows:[],next:null,mode:'live',year:2026,from:'2026-01-01',to:'2026-12-31'});await h.flush();
    const form=h.all().find(node=>node.type==='form');form.props.onSubmit({preventDefault(){}});await h.flush();
    assert.equal(h.url().searchParams.get('viewer'),'member');assert.equal(writes(h).length,0);
  }
});

test('budget project and structural detail links preserve preview and close before membership resolves',async t=>{
  const h=harness('../app/budget/project/page.tsx',{role:preview(),path:'/budget/project?service=QWGJK&key=TEST&viewer=member'});t.after(()=>h.dispose());await h.flush();
  assert.equal(paths(h)[0].searchParams.get('viewer'),'member');
  for(const link of h.links())assert.equal(new URL(link.props.href,'https://g2b.test').searchParams.get('viewer'),'member');
  const structural=harness('../app/budget/components/structural-links.tsx',{role:preview(),props:{mode:'live',sourceKey:'STRUCTURE'}});t.after(()=>structural.dispose());await structural.flush();
  assert.equal(paths(structural)[0].searchParams.get('viewer'),'member');
  const denied=harness('../app/budget/project/page.tsx',{role:closed(),path:'/budget/project?service=QWGJK&key=TEST'});t.after(()=>denied.dispose());await denied.flush();assert.equal(denied.requests.length,0);
});
