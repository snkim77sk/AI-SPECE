import test from 'node:test';
import assert from 'node:assert/strict';
import {MEMBER_READ_PATHS,accessDecision} from '../lib/public-auth-policy.mjs';
const member={role:'member',status:'approved'},admin={role:'admin',status:'approved'};

test('every reviewed stored-data route allows reads and denies all member mutations',()=>{
 for(const path of MEMBER_READ_PATHS){
  for(const method of ['GET','HEAD'])assert.equal(accessDecision(path,method,member),'allow',method+' '+path);
  for(const method of ['POST','PUT','PATCH','DELETE','OPTIONS'])assert.equal(accessDecision(path,method,member),'forbidden',method+' '+path);
 }
});

test('new routes, management pages, runtime diagnostics and operation APIs stay administrator-only',()=>{
 const paths=['/api/new-management-route','/new-owner-screen','/settings','/settings/','/collection-monitor',
  '/program-link','/agent-lab','/api/runtime-info','/owner-progress','/admin/members','/api/release-readiness',
  '/api/automation','/api/collection','/api/collection/recovery','/api/server-status','/api/server-sync',
  '/api/storage-inspection','/storage-inspection','/api/maintenance/synthetic','/api/public-auth/admin'];
 for(const path of paths){
  for(const method of ['GET','HEAD','POST','PUT','PATCH','DELETE']){
   assert.equal(accessDecision(path,method,member),'forbidden',method+' '+path);
   assert.equal(accessDecision(path,method,admin),'allow',method+' '+path);
  }
 }
 for(const principal of [null,{role:'member',status:'pending'},{role:'admin',status:'suspended'},{role:'unknown',status:'approved'}]){
  for(const path of MEMBER_READ_PATHS)assert.notEqual(accessDecision(path,'GET',principal),'allow',path);
 }
});

test('sign-in and static assets only allow reads, while logout remains a scoped POST',()=>{
 for(const path of ['/login','/account/pending','/api/public-auth/me','/api/public-auth/readiness','/api/public-auth/start/naver','/api/public-auth/callback/google','/favicon.svg','/_next/static/chunks/page.js']){
  assert.equal(accessDecision(path,'GET',null),'allow');
  assert.notEqual(accessDecision(path,'POST',null),'allow');
 }
 assert.equal(accessDecision('/api/public-auth/logout','POST',member),'allow');
 assert.notEqual(accessDecision('/api/public-auth/logout','GET',null),'allow');
 for(const path of MEMBER_READ_PATHS)assert.notEqual(accessDecision(path,'POST',null,true),'allow');
});
