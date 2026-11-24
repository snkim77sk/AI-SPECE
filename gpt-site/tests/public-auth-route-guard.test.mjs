import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {join,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../app/api/',import.meta.url));
function routes(dir){
 return readdirSync(dir,{withFileTypes:true}).flatMap(ent=>{
  const path=join(dir,ent.name);
  return ent.isDirectory()?routes(path):ent.name==='route.ts'?[path]:[];
 });
}
test('all existing data and mutation handlers have local auth guard, not only proxy',()=>{
 const all=routes(root).filter(path=>relative(root,path).split(sep)[0]!=='public-auth');
 const retired=[];
 for(const path of all){
  const contents=readFileSync(path,'utf8');
  if(/export const GET\s*=\s*retired/.test(contents)&&/export const POST\s*=\s*retired/.test(contents)){retired.push(path);continue;}
  const handlers=[...contents.matchAll(/export\s+async\s+function\s+(GET|POST|PUT|PATCH|DELETE)\s*\([^)]*\)\s*\{/g)];
  assert.ok(handlers.length>0,path+': expected named route handlers');
  assert.match(contents,/guardPublicRequest/,path+': missing independent auth import');
  assert.ok((contents.match(/__publicBlock=await guardPublicRequest/g)||[]).length>=handlers.length,path+': unguarded handler');
 }
 assert.equal(retired.length,1);
});
test('administrative authorization is checked again inside admin endpoint',()=>{
 const src=readFileSync(new URL('../app/api/public-auth/admin/route.ts',import.meta.url),'utf8');
 assert.match(src,/requireAdmin/);
 assert.match(src,/Origin|origin/);
 assert.match(src,/role='member'/);
 assert.doesNotMatch(src,/UPDATE public_members SET role=/);
});
