import test from 'node:test';
import assert from 'node:assert/strict';
import {parseGenerationJson} from '../lib/g2b-generation-json.mjs';

const reject=(text,code=/GENERATION_SOURCE_INVALID/)=>assert.throws(()=>parseGenerationJson(text),code,text.slice(0,120));

test('strict scanner accepts standard JSON values and ordinary whitespace',()=>{
 for(const text of ['null','true','false','0','-0','1','-12','1.25','1e2','1E+2','-1.2E-2','""','"plain"','[]','{}','[1,true,false,null,"x"]','{"a":[],"b":{},"c":{"d":[1]}}',' \r\n\t {"x":1} \r\n\t '])assert.deepEqual(parseGenerationJson(text),JSON.parse(text));
 assert.ok(Object.is(parseGenerationJson('-0'),-0));
});
test('malformed grammar is rejected rather than accepted by partial token scans',()=>{
 const malformed=['',' ','undefined','NaN','Infinity','-Infinity','TRUE','False','nul','truefalse','null null','{}{}','[]x','[1]false','{','}','[',']','{"a"}','{"a":}','{"a" 1}','{a:1}',"{'a':1}",'{"a":1,}','{,"a":1}','{"a":1 "b":2}','{"a":1,,"b":2}','[1,]','[,1]','[1,,2]','[1 2]','[1:2]','+1','.1','1.','01','-01','00','--1','1e','1e+','1e-','1e2e3','1.e2','0x10','0b10','0o10','1_000','-','/*comment*/1','1//comment','\u00a0null'];
 for(const text of malformed)reject(text);
});
test('string grammar accepts all JSON escapes and rejects invalid/unterminated escapes',()=>{
 const text='{"quote":"\\\"","slash":"\\/","backslash":"\\\\","control":"\\b\\f\\n\\r\\t","unicode":"\\uD55C\\uAE00","emoji":"\\uD83D\\uDE00"}';
 assert.deepEqual(parseGenerationJson(text),JSON.parse(text));
 for(const value of ['"','"abc','"abc\\','"\\x20"','"\\v"','"\\a"','"\\u123"','"\\u12XZ"','"\\u"','"a\nb"','"a\rb"','"a\tb"','"a\u0000b"'])reject(value);
});
test('duplicate object keys reject plain, escaped and differently escaped equivalents',()=>{
 for(const text of ['{"a":1,"a":2}','{"a":1,"\\u0061":2}','{"\\u0061":1,"a":2}','{"a/b":1,"a\\/b":2}','{"\\n":1,"\\u000a":2}','{"😀":1,"\\uD83D\\uDE00":2}','{"":1,"":2}','{"__proto__":1,"\\u005f_proto__":2}'])reject(text,/GENERATION_SOURCE_DUPLICATE_KEY/);
});
test('duplicate detection is scoped per object and traverses all nested arrays/objects',()=>{
 const valid='{"a":{"x":1},"b":{"x":2},"items":[{"x":3},{"x":4}]}';assert.deepEqual(parseGenerationJson(valid),JSON.parse(valid));
 for(const text of ['{"a":[{"x":1,"x":2}]}','[[[{"x":1,"\\u0078":2}]]]','{"safe":0,"unknown":{"nested":[{"duplicate":1,"duplicate":2}]}}'])reject(text,/DUPLICATE_KEY/);
});
test('shadowed provider identity, totals and response containers are rejected before use',()=>{
 for(const text of [
  '{"response":{"body":{"items":[{"dlvrReqNo":"first","dlvrReqNo":"second"}]}}}',
  '{"response":{"body":{"items":[{"dlvrReqNo":"first","\\u0064lvrReqNo":"second"}]}}}',
  '{"response":{"body":{"totalCount":1,"totalCount":2}}}',
  '{"response":{"body":{"pageNo":1,"\\u0070ageNo":2}}}',
  '{"response":{"body":{},"body":{"totalCount":0}}}',
  '{"response":{"body":{"items":[],"items":[1]}}}',
  '{"response":{},"response":{"body":{"totalCount":0}}}'
 ])reject(text,/DUPLICATE_KEY/);
});
test('number safety enforces integer boundaries, significant digits, range and underflow',()=>{
 for(const token of ['9007199254740991','-9007199254740991','0','-0','123456789012345','1.23456789012345','0.00000123456789012345','1e15','1e-300','0e-400','-0.0e999'])assert.deepEqual(parseGenerationJson(token),JSON.parse(token));
 for(const token of ['9007199254740992','-9007199254740992','9007199254740993','9007199254740991.1','1.234567890123456','1e309','-1e309','1e-400','-1e-400','1e16','9999999999999999999999','1e'+'0'.repeat(129)])reject(token,/SOURCE_NUMBER_OUT_OF_RANGE/);
});
test('numeric and duplicate-looking content inside strings is not treated as syntax',()=>{
 const value={numeric:'9007199254740993 1e-400 1.234567890123456789',json:'{"x":1,"x":2}',escaped:'\\"a\\":123',unknown:'SECRET test string',nested:{'9007199254740993':'1e999'}};assert.deepEqual(parseGenerationJson(JSON.stringify(value)),value);
});
test('unknown fields, __proto__ data and Unicode keys are preserved without prototype mutation',()=>{
 const text='{"unknown":{"nested":[1,true,null,"value"]},"__proto__":{"polluted":true},"constructor":"data","한글":"조명","é":1,"é":2}';const result=parseGenerationJson(text);assert.deepEqual(result,JSON.parse(text));assert.equal(Object.getPrototypeOf(result),Object.prototype);assert.equal(Object.hasOwn(result,'__proto__'),true);assert.equal({}.polluted,undefined);assert.equal(result['é'],1);assert.equal(result['é'],2);
});
test('depth boundary and node budget stop excessive nesting or work',()=>{
 const atLimit='['.repeat(32)+'0'+']'.repeat(32);assert.doesNotThrow(()=>parseGenerationJson(atLimit));reject('['.repeat(33)+'0'+']'.repeat(33),/SOURCE_CANONICAL_LIMIT/);
 const many='['+'0,'.repeat(250000)+'0]';reject(many,/SOURCE_CANONICAL_LIMIT/);
});
test('numeric token length is bounded before expensive conversion',()=>{
 reject('9'.repeat(129),/SOURCE_NUMBER_OUT_OF_RANGE/);reject('0.'+'0'.repeat(127)+'1',/SOURCE_NUMBER_OUT_OF_RANGE/);
});
