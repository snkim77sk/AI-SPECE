import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
registerHooks({resolve(spec,ctx,next){
 if(spec.startsWith('.')&&!/\.(ts|mjs|js)$/.test(spec)&&ctx.parentURL?.startsWith('file:')){
  const candidate=new URL(spec+'.ts',ctx.parentURL);if(existsSync(fileURLToPath(candidate)))return next(candidate.href,ctx);
 }
 return next(spec,ctx);
}});
const helper=await import('../lib/viewer-access.ts');
const account=(role='member',status='approved')=>({authenticated:true,member:{displayName:'사용자',role,status}});
function harness({publicMembership=true,path='/dashboard',query='',timeout=15000}={}){
 const slots=[],effects=[],requests=[];let index=0,dirty=true,tree;
 const hooks={
  createContext:()=>({Provider:'provider'}),useContext:()=>{throw Error('NOT_EXPECTED');},
  useState(initial){const n=index++;if(!(n in slots))slots[n]=initial;return [slots[n],value=>{slots[n]=typeof value==='function'?value(slots[n]):value;dirty=true;}];},
  useMemo(fn,deps){const n=index++,last=slots[n];if(!last||deps.some((v,i)=>!Object.is(v,last.deps[i])))slots[n]={deps,value:fn()};return slots[n].value;},
  useEffect(fn,deps){const n=index++,last=slots[n];if(!last||deps.some((v,i)=>!Object.is(v,last.deps[i]))){const next={effect:true,deps,cleanup:null};slots[n]=next;effects.push(()=>{last?.cleanup?.();next.cleanup=fn();});}},
 };
 const imports={react:hooks,'react/jsx-runtime':{jsx:(type,props)=>({type,props})},'next/navigation':{usePathname:()=>path,useSearchParams:()=>new URLSearchParams(query)},
  '../lib/viewer-access':{...helper,fetchViewerAccount:(fetcher,signal)=>helper.fetchViewerAccount(fetcher,signal,timeout)}};
 const source=readFileSync(new URL('../components/viewer-access.tsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};
 vm.runInNewContext(code,{exports,require:name=>{assert.ok(name in imports,name);return imports[name];},AbortController,
  fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve:data=>resolve(Response.json(data))}))});
 function render(){index=0;dirty=false;tree=exports.ViewerAccessProvider({children:'child',publicMembership});while(effects.length)effects.shift()();}
 async function flush(){for(let n=0;n<24;n++){await Promise.resolve();if(dirty)render();}}
 render();
 return {requests,flush,value:()=>tree.props.value,change(next){path=next.path??path;query=next.query??query;publicMembership=next.publicMembership??publicMembership;dirty=true;},dispose(){for(const slot of slots)if(slot?.effect)slot.cleanup?.();}};
}

test('actual provider keeps unresolved users closed and gives approved members only data access',async()=>{
 const h=harness();
 try{
  assert.equal(h.value().canRead,false);assert.equal(h.value().canManage,false);assert.equal(h.value().loading,true);
  h.requests[0].resolve(account());await h.flush();
  assert.equal(h.value().canRead,true);assert.equal(h.value().canManage,false);assert.equal(h.value().loading,false);
  h.change({path:'/budget'});await h.flush();
  assert.equal(h.value().canRead,false);assert.equal(h.value().canManage,false);
  h.requests[1].resolve(account('admin','pending'));await h.flush();
  assert.equal(h.value().canRead,false);assert.equal(h.value().isAdministrator,false);
 }finally{h.dispose();}
});

test('navigation cancels old membership and a late administrator response cannot restore rights',async()=>{
 const h=harness();
 try{
  h.change({path:'/budget',query:'viewer=member&view=all'});await h.flush();
  assert.equal(h.requests[0].options.signal.aborted,true);
  h.requests[1].resolve(account());await h.flush();h.requests[0].resolve(account('admin'));await h.flush();
  assert.equal(h.value().canManage,false);assert.equal(h.value().isAdministrator,false);assert.equal(h.value().canRead,true);
 }finally{h.dispose();}
});

test('private preview reduces privileges, keeps business filters and never requests membership',async()=>{
 const h=harness({publicMembership:false});
 try{
  assert.equal(h.value().canManage,true);assert.equal(h.requests.length,0);
  h.change({query:'viewer=member'});await h.flush();
  assert.equal(h.value().canManage,false);assert.equal(h.value().preview,true);assert.equal(h.value().isAdministrator,true);
  assert.equal(h.value().href('/api/deliveries?view=history'),'/api/deliveries?view=history&viewer=member');
  h.change({query:''});await h.flush();assert.equal(h.value().canManage,true);assert.equal(h.requests.length,0);
 }finally{h.dispose();}
});

test('account timeout closes capabilities and releases loading; fixture lab sends no account request',async()=>{
 const h=harness({timeout:10});
 try{
  await new Promise(resolve=>setTimeout(resolve,25));await h.flush();
  assert.equal(h.value().loading,false);assert.equal(h.value().canRead,false);assert.equal(h.value().canManage,false);
  assert.match(h.value().error,/15초/);
 }finally{h.dispose();}
 const lab=harness({path:'/agent-lab'});
 try{await lab.flush();assert.equal(lab.requests.length,0);assert.equal(lab.value().canManage,false);}finally{lab.dispose();}
});
