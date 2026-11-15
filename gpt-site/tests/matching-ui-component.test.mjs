import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
import * as helpers from '../lib/matching-ui.mjs';

// Execute the real component with a deterministic hook/JSX host and stubbed fetch.
// No browser, database, server or provider is started or contacted.
function harness(search='?mode=synthetic') {
 const slots=[],effects=[],requests=[],listeners=new Map();let index=0,dirty=true,tree,url=new URL('https://example.test/matching'+search);
 const runtime={jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})};
 const hooks={
  useState(initial){const n=index++;if(!(n in slots))slots[n]=initial;return [slots[n],value=>{slots[n]=typeof value==='function'?value(slots[n]):value;dirty=true;}];},
  useRef(initial){const n=index++;if(!(n in slots))slots[n]={current:initial};return slots[n];},
  useEffect(fn,deps){const n=index++;if(!(n in slots)){slots[n]=deps;effects.push(fn);}},
 };
 const window={get location(){return {href:url.href,search:url.search};},history:{state:null,replaceState(state,_,next){this.state=state;url=new URL(next,url);}},dispatchEvent(event){listeners.get(event.type)?.();},addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(type){listeners.delete(type);}};
 const fetch=(target,options)=>new Promise((resolve,reject)=>requests.push({target,options,resolve:data=>resolve({ok:true,json:async()=>data}),reject}));
 const source=readFileSync(new URL('../app/matching/page.tsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};vm.runInNewContext(code,{exports,require:name=>name==='react'?hooks:name==='react/jsx-runtime'?runtime:name==='next/navigation'?{useSearchParams:()=>url.searchParams}:helpers,fetch,window,URL,URLSearchParams,AbortSignal,Event});
 function render(){index=0;dirty=false;tree=exports.default();while(effects.length)effects.shift()();}
 async function flush(){for(let n=0;n<10;n++){await Promise.resolve();if(dirty)render();}}
 function all(node,out=[]){if(Array.isArray(node)){node.forEach(x=>all(x,out));return out;}if(node&&typeof node==='object'){out.push(node);all(node.props?.children,out);}return out;}
 function text(node){return typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):'';}
 function control(label){return all(tree).find(x=>x.props?.['aria-label']===label);}
 function button(label){return all(tree).find(x=>x.type==='button'&&text(x)===label);}
 render();
 return {requests,flush,control,button,text:()=>text(tree),all:()=>all(tree),url:()=>url,back(mode){url=new URL('/matching?mode='+mode,url);listeners.get('popstate')?.();}};
}
const job=(id='s',mode='synthetic')=>({id,mode,state:'complete',phase:'DONE',from_day:'2026-01-01',to_day:'2026-01-02',budget_count:1,request_count:1,candidate_count:1,match_count:1,excluded_count:0});
const workspace=(selected,jobs=[selected])=>({job:selected,jobs,window:{from:'2026-01-01',to:'2026-11-01'}});
const rows=(selected,label='SYNTHETIC_SENTINEL')=>({job:selected,rows:[{shopping_source_key:'x',budget_project:label,evidence:[]}],next:'cursor-one'});

test('real matching component reads synthetic URL, rejects late results after mode switch, and preserves header mode',async()=>{
 const h=harness();await h.flush();assert.equal(h.control('매칭 자료').props.value,'synthetic');
 h.requests[0].resolve(workspace(job()));await h.flush();assert.match(h.requests[1].target,/action=results/);
 h.control('매칭 자료').props.onChange({target:{value:'live'}});await h.flush();
 h.requests[1].resolve(rows(job()));await h.flush();
 assert.doesNotMatch(h.text(),/SYNTHETIC_SENTINEL/);assert.equal(h.control('매칭 자료').props.value,'live');
 assert.equal(h.url().searchParams.get('mode'),'live');
 assert.ok(h.all().some(x=>x.type==='a'&&x.props.href==='/budget?mode=live'));
 assert.equal(h.button('다음100건').props.disabled,true);
});
test('real matching component suppresses old workspace error after a newer mode read',async()=>{
 const h=harness();await h.flush();h.control('매칭 자료').props.onChange({target:{value:'live'}});await h.flush();
 h.requests[0].reject(Error('OLD_ERROR_SENTINEL'));h.requests[1].resolve(workspace(job('l','live')));await h.flush();
 h.requests[2].resolve(rows(job('l','live'),'LIVE_SENTINEL'));await h.flush();
 assert.doesNotMatch(h.text(),/OLD_ERROR_SENTINEL/);assert.match(h.text(),/LIVE_SENTINEL/);
});
test('real matching component does not adopt another mode latest job or submit its continuation',async()=>{
 const h=harness();await h.flush();h.requests[0].resolve(workspace(job('l','live')));await h.flush();
 assert.equal(h.requests.length,1);assert.equal(h.button('연속 분석').props.disabled,true);
 h.button('연속 분석').props.onClick();await h.flush();
 assert.equal(h.requests.length,1);assert.match(h.text(),/현재 자료 모드의 계획/);
 h.button('상태 새로고침').props.onClick();await h.flush();assert.equal(h.requests[1].target,'/api/matching');
});
test('real matching component Back and score change clear the cursor and obsolete pages',async()=>{
 const h=harness();await h.flush();h.requests[0].resolve(workspace(job()));await h.flush();h.requests[1].resolve(rows(job()));await h.flush();
 assert.equal(h.button('다음100건').props.disabled,false);
 h.control('매칭 점수 분류').props.onChange({target:{value:'HIGH'}});await h.flush();
 assert.equal(h.button('다음100건').props.disabled,true);assert.doesNotMatch(h.text(),/SYNTHETIC_SENTINEL/);
 assert.match(h.requests[2].target,/level=HIGH/);assert.doesNotMatch(h.requests[2].target,/after=/);
 h.back('live');await h.flush();h.requests[2].resolve(rows(job()));await h.flush();
 assert.equal(h.control('매칭 자료').props.value,'live');assert.doesNotMatch(h.text(),/SYNTHETIC_SENTINEL/);
});
test('real matching component does not continue a write response after Back changes selection',async()=>{
 const h=harness();await h.flush();h.requests[0].resolve(workspace(job()));await h.flush();h.requests[1].resolve(rows(job()));await h.flush();
 h.button('새 매칭 계획').props.onClick();await h.flush();
 const write=h.requests[2];assert.equal(write.options.method,'POST');assert.equal(JSON.parse(write.options.body).mode,'synthetic');
 h.back('live');await h.flush();assert.equal(h.requests.length,4);
 write.resolve(workspace({...job('new','synthetic'),state:'ready'}));await h.flush();
 assert.equal(h.requests.length,4);assert.equal(h.control('매칭 자료').props.value,'live');assert.doesNotMatch(h.text(),/SYNTHETIC_SENTINEL/);
});
test('real matching component rejects mismatched result identity rather than displaying it',async()=>{
 const h=harness();await h.flush();h.requests[0].resolve(workspace(job()));await h.flush();h.requests[1].resolve(rows(job('other','live'),'WRONG_RESULT_SENTINEL'));await h.flush();
 assert.doesNotMatch(h.text(),/WRONG_RESULT_SENTINEL/);assert.match(h.text(),/MATCH_RESULT_MODE_MISMATCH/);assert.equal(h.button('다음100건').props.disabled,true);
});
