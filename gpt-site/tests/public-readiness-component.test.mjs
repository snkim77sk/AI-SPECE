import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as helpers from '../lib/public-readiness-response.ts';

const stamp='2026-11-25T01:00:00.000Z';
const owner=()=>({schema:'g2b-release-readiness-v1',checkedAt:stamp,enabled:true,
 configuration:{httpsOrigin:true,sessionSecret:true,serviceSyncToken:true},identityStorage:{schema:'ready',approvedAdminPresent:false},
 providers:['kakao','naver','google'].map(id=>({id,configured:true,loginAvailable:true,realLogin:'unverified'})),
 approvalFlow:'unverified',windowsServiceCompatibility:'unverified',publicRelease:'unverified'});
const signin=()=>({schema:'g2b-signin-availability-v1',checkedAt:stamp,state:'available',providers:[{id:'kakao',available:false},{id:'naver',available:false},{id:'google',available:true}]});

// Run the actual TSX with deterministic hooks and controlled network responses.
function harness(path,timeoutMs=15000){
 const slots=[],effects=[],requests=[];let index=0,dirty=true,tree,props={refreshKey:0};
 const hooks={
  useState(initial){const n=index++;if(!(n in slots))slots[n]=initial;return [slots[n],value=>{slots[n]=typeof value==='function'?value(slots[n]):value;dirty=true;}];},
  useEffect(fn,deps){
   const n=index++,previous=slots[n];
   if(!previous||deps.some((value,i)=>!Object.is(value,previous.deps[i]))){
    const next={kind:'effect',deps,cleanup:null};slots[n]=next;
    effects.push(()=>{previous?.cleanup?.();next.cleanup=fn();});
   }
  },
 };
 const jsx={jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})};
 const bounded={...helpers,
  fetchReleaseReadiness:(fetcher,signal)=>helpers.fetchReleaseReadiness(fetcher,signal,timeoutMs),
  fetchSignInAvailability:(fetcher,signal)=>helpers.fetchSignInAvailability(fetcher,signal,timeoutMs)};
 const fetch=(target,options)=>new Promise((resolve,reject)=>requests.push({target,options,
  resolve:data=>resolve(Response.json(data)),response:resolve,reject}));
 const source=readFileSync(new URL(path,import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};
 vm.runInNewContext(code,{exports,require:name=>name==='react'?hooks:name==='react/jsx-runtime'?jsx:bounded,
  fetch,window:{location:{search:''}},URLSearchParams,AbortController});
 function render(){index=0;dirty=false;tree=exports.default(props);while(effects.length)effects.shift()();}
 async function flush(){for(let i=0;i<20;i++){await Promise.resolve();if(dirty)render();}}
 function all(node,out=[]){if(Array.isArray(node)){node.forEach(item=>all(item,out));return out;}if(node&&typeof node==='object'){out.push(node);all(node.props?.children,out);}return out;}
 function text(node){return typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):'';}
 render();
 return {requests,flush,text:()=>text(tree),all:()=>all(tree),button:label=>all(tree).find(node=>node.type==='button'&&text(node)===label),
  refreshParent(){props={refreshKey:props.refreshKey+1};dirty=true;},
  dispose(){for(const slot of slots)if(slot?.kind==='effect')slot.cleanup?.();}};
}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));

test('owner component clears old successful evidence on refresh and keeps failures retryable',async()=>{
 const h=harness('../components/public-release-readiness.tsx');
 try{
  h.requests[0].resolve(owner());await h.flush();
  assert.match(h.text(),/기본 인증 설정/);assert.match(h.text(),/최초 관리자 지정 필요/);
  assert.ok(h.all().some(node=>node.props?.id==='public-readiness'));
  h.button('준비 상태 새로고침').props.onClick();await h.flush();
  assert.doesNotMatch(h.text(),/기본 인증 설정|최초 관리자 지정 필요/);
  h.requests[1].response(Response.json({error:'PRIVATE'}, {status:503}));await h.flush();
  assert.match(h.text(),/불러오지 못했습니다/);assert.doesNotMatch(h.text(),/PRIVATE|기본 인증 설정/);
  assert.equal(h.button('준비 상태 새로고침').props.disabled,false);
  h.button('준비 상태 새로고침').props.onClick();await h.flush();h.requests[2].resolve(owner());await h.flush();
  assert.match(h.text(),/기본 인증 설정/);
 }finally{h.dispose();}
});

test('parent refresh cancels obsolete responses rather than restoring stale readiness',async()=>{
 const h=harness('../components/public-release-readiness.tsx');
 try{
  await h.flush();h.refreshParent();await h.flush();
  assert.equal(h.requests[0].options.signal.aborted,true);
  const unknown={...owner(),identityStorage:{schema:'unknown',approvedAdminPresent:null},providers:owner().providers.map(provider=>({...provider,loginAvailable:false}))};
  h.requests[1].resolve(unknown);await h.flush();h.requests[0].resolve(owner());await h.flush();
  assert.match(h.text(),/조회 실패 · 미확인/);assert.doesNotMatch(h.text(),/최초 관리자 지정 필요/);
 }finally{h.dispose();}
});

test('owner request and JSON-body stalls both release the refresh button after the deadline',async()=>{
 const h=harness('../components/public-release-readiness.tsx',10);
 try{
  await wait(25);await h.flush();assert.match(h.text(),/15초 안에/);
  assert.equal(h.button('준비 상태 새로고침').props.disabled,false);
  h.button('준비 상태 새로고침').props.onClick();await h.flush();
  h.requests[1].response({ok:true,json:()=>new Promise(()=>{})});
  await wait(25);await h.flush();assert.match(h.text(),/15초 안에/);
  assert.equal(h.button('준비 상태 새로고침').props.disabled,false);
 }finally{h.dispose();}
});

test('login renders only configured provider links and never retains them after a failed refresh',async()=>{
 const h=harness('../app/login/page.tsx');
 try{
  h.requests[0].resolve(signin());await h.flush();
  assert.deepEqual(h.all().filter(node=>node.type==='a').map(node=>node.props.href),['/api/public-auth/start/google']);
  assert.equal(h.all().filter(node=>node.type==='button'&&node.props.disabled).length,3);
  h.button('로그인 준비 상태 다시 확인').props.onClick();await h.flush();
  assert.equal(h.all().filter(node=>node.type==='a').length,0);
  h.requests[1].response(Response.json({error:'PRIVATE'},{status:503}));await h.flush();
  assert.match(h.text(),/불러오지 못했습니다/);assert.doesNotMatch(h.text(),/현재 소셜 회원 서비스가 꺼져/);
  assert.equal(h.button('로그인 준비 상태 다시 확인').props.disabled,false);
 }finally{h.dispose();}
});

test('login rejects a successful HTTP response with unrelated JSON and distinguishes explicit disabled status',async()=>{
 const h=harness('../app/login/page.tsx');
 try{
  h.requests[0].resolve({authenticated:false,member:null});await h.flush();
  assert.equal(h.all().filter(node=>node.type==='a').length,0);assert.match(h.text(),/불러오지 못했습니다/);
  h.button('로그인 준비 상태 다시 확인').props.onClick();await h.flush();
  h.requests[1].resolve({...signin(),state:'disabled',providers:signin().providers.map(provider=>({...provider,available:false}))});await h.flush();
  assert.match(h.text(),/현재 소셜 회원 서비스가 꺼져/);
  assert.equal(h.all().filter(node=>node.type==='a').length,0);
 }finally{h.dispose();}
});
