import test from 'node:test';
import assert from 'node:assert/strict';
import {accountResponse,reviewMembers} from '../lib/public-auth-response.ts';
test('account JSON distinguishes guests and every supported member state',()=>{
 assert.deepEqual(accountResponse({authenticated:false,member:null}),{authenticated:false,member:null});
 for(const status of ['pending','approved','rejected','suspended'])assert.equal(accountResponse({authenticated:true,member:{displayName:'회원',role:'member',status}}).member.status,status);
});
test('malformed and error responses cannot become a displayed approval state',()=>{
 for(const value of [null,[],{error:'PUBLIC_AUTH_NOT_READY'},{authenticated:true},{authenticated:true,member:{displayName:'회원',status:'unknown',role:'member'}}])assert.throws(()=>accountResponse(value));
 const member={id:'id',display_name:'회원',email:'',status:'pending',role:'member',created_at:'2026-11-24'};
 assert.deepEqual(reviewMembers({members:[member]}),[member]);
 for(const value of [null,{error:'ADMIN_REQUIRED'},{members:[null]},{members:[{...member,status:'unexpected'}]},{members:Array(101).fill(member)}])assert.throws(()=>reviewMembers(value));
});
