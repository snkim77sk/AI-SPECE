import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createMatchingReadGuard,matchingMode,matchingJobMatches} from '../lib/matching-ui.mjs';

test('matching mode is restored only from an explicit synthetic URL',()=>{
 assert.equal(matchingMode('?mode=synthetic'),'live');
 for(const query of ['', '?mode=live', '?mode=unknown'])assert.equal(matchingMode(query),'live');
});
test('selected and returned jobs must agree with mode and exact job identity',()=>{
 const job={id:'a',mode:'synthetic'};
 assert.equal(matchingJobMatches(job,'synthetic','a'),true);
 assert.equal(matchingJobMatches(job,'live','a'),false);
 assert.equal(matchingJobMatches(job,'synthetic','b'),false);
 assert.equal(matchingJobMatches(null,'synthetic'),false);
});
test('mode changes, job selection and unmount invalidate all late responses and errors',()=>{
 const guard=createMatchingReadGuard();
 const workspace=guard.begin('workspace'),results=guard.begin('results'),write=guard.begin('write');
 guard.invalidate();
 for(const token of [workspace,results,write])assert.equal(guard.current(token),false);
 assert.equal(guard.current(guard.begin('results')),true);
});
test('repeated refresh and result requests keep only the newest response',()=>{
 const guard=createMatchingReadGuard();
 const olderWorkspace=guard.begin('workspace'),newerWorkspace=guard.begin('workspace');
 const olderResults=guard.begin('results'),newerResults=guard.begin('results');
 assert.equal(guard.current(olderWorkspace),false);
 assert.equal(guard.current(olderResults),false);
 assert.equal(guard.current(newerWorkspace),true);
 assert.equal(guard.current(newerResults),true);
});
test('refresh and score edits can fence results without cancelling their own workspace or write token',()=>{
 const guard=createMatchingReadGuard(),workspace=guard.begin('workspace'),write=guard.begin('write'),old=guard.begin('results');
 guard.begin('results');
 assert.equal(guard.current(old),false);
 assert.equal(guard.current(workspace),true);
 assert.equal(guard.current(write),true);
});
test('deferred synthetic success and error are suppressed after selecting live',async()=>{
 const guard=createMatchingReadGuard();let release;const pending=new Promise(resolve=>{release=resolve;});
 const token=guard.begin('results');const displayed=[];
 const response=pending.then(()=>{if(guard.current(token))displayed.push('synthetic rows');});
 guard.invalidate();release();await response;
 assert.deepEqual(displayed,[]);
 const failure=guard.begin('workspace');guard.invalidate();
 if(guard.current(failure))displayed.push('old error');
 assert.deepEqual(displayed,[]);
});
test('matching component wires URL, guarded reads, POST identity and explicit navigation',()=>{
 const source=readFileSync(new URL('../app/matching/page.tsx',import.meta.url),'utf8');
 assert.match(source,/selectMode\(matchingMode\(window.location.search\),false\)/);
 assert.match(source,/addEventListener\('popstate',restore\)/);
 assert.match(source,/dispatchEvent\(new Event\('g2b:mode-change'\)\)/);
 assert.match(source,/matchingJobMatches\(data.job,expectedMode,target\)/);
 assert.match(source,/currentMode.current!==expectedMode\|\|id.current!==target/);
 assert.match(source,/action!=='create'&&!matchingJobMatches\(currentJob.current,expectedMode,target\)/);
 assert.match(source,/JSON.stringify\(\{action,id:target,mode:expectedMode,from,to\}\)/);
 assert.match(source,/function clearResults\(\) \{setRows\(\[\]\);setNext\(null\)/);
 assert.match(source,/if\(!locked.current\)void workspace\(id.current\)/);
 assert.match(source,/href=\{'\/budget\?mode='\+mode\}/);
 assert.match(source,/href=\{'\/shopping\?category=LIGHTING&mode='\+mode\}/);
 assert.doesNotMatch(source,/href="\/(budget)?"/);
});
