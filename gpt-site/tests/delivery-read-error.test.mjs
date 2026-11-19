import test from 'node:test';
import assert from 'node:assert/strict';
import {deliveryReadNotice} from '../lib/delivery-read-error.mjs';

test('safe delivery API diagnostics distinguish storage failures and preserve incident IDs',()=>{
 const incident='12345678-abcd-4321-9abc-1234567890ab';
 const msg=deliveryReadNotice(503,{error:'DATABASE_RESOURCE_LIMIT',incident,sql:'SELECT secret FROM private'});
 assert.match(msg,/조회 저장소/);
 assert.match(msg,/DATABASE_RESOURCE_LIMIT/);
 assert.match(msg,/진단번호 12345678-abcd-4321-9abc-1234567890ab/);
 assert.doesNotMatch(msg,/SELECT|private/);
});
test('invalid dates are user input errors rather than absence of data',()=>{
 assert.match(deliveryReadNotice(400,{error:'INVALID_DATE_RANGE'}),/조회기간/);
 assert.match(deliveryReadNotice(400,{error:'INVALID_DATE_RANGE'}),/INVALID_DATE_RANGE/);
});
test('network and timeout errors remain distinct from missing stored records',()=>{
 assert.match(deliveryReadNotice(0,null,{name:'TimeoutError'}),/15초/);
 assert.match(deliveryReadNotice(0,null,{name:'TypeError'}),/통신/);
 assert.match(deliveryReadNotice(200,null,{message:'INVALID_READ_RESPONSE'}),/응답 형식/);
});
test('unsafe backend error text and incident are not reflected into the browser',()=>{
 const text=deliveryReadNotice(503,{error:'SQL SELECT * FROM data',incident:'1234<script>alert(1)</script>'});
 assert.doesNotMatch(text,/SELECT|script|alert|data/);
 assert.match(text,/HTTP 503/);
});
