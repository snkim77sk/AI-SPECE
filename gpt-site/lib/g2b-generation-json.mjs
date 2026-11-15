/** Strict bounded JSON syntax scan before JSON.parse. Duplicate decoded keys and
 * numeric lexemes that would silently lose significant precision are rejected.
 * This is not an authoritative provider snapshot or a signed wire format. */
export function parseGenerationJson(text){
 let i=0,nodes=0;const n=text.length;
 const fail=()=>{throw Error('GENERATION_SOURCE_INVALID');};
 const space=()=>{while(i<n&&/[\t\n\r ]/.test(text[i]))i++;};
 function string(decode=false){
  if(text[i]!=='"')fail();const start=i++;
  while(i<n){const c=text.charCodeAt(i++);if(c===34){const s=text.slice(start,i);return decode?JSON.parse(s):null;}if(c<32)fail();if(c===92){if(i>=n)fail();const esc=text[i++];if(esc==='u'){if(!/^[0-9a-fA-F]{4}$/.test(text.slice(i,i+4)))fail();i+=4;}else if(!'"\\/bfnrt'.includes(esc))fail();}}
  fail();
 }
 function value(depth){
  if(depth>32||++nodes>250000)throw Error('SOURCE_CANONICAL_LIMIT');space();
  if(text[i]==='{'){
   i++;space();const keys=new Set();if(text[i]==='}'){i++;return;}
   while(true){space();const key=string(true);if(keys.has(key))throw Error('GENERATION_SOURCE_DUPLICATE_KEY');keys.add(key);space();if(text[i++]!==':')fail();value(depth+1);space();const c=text[i++];if(c==='}')return;if(c!==',')fail();}
  }
  if(text[i]==='['){i++;space();if(text[i]===']'){i++;return;}while(true){value(depth+1);space();const c=text[i++];if(c===']')return;if(c!==',')fail();}}
  if(text[i]==='"'){string();return;}
  for(const literal of ['true','false','null'])if(text.startsWith(literal,i)){i+=literal.length;return;}
  const number=/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;number.lastIndex=i;const m=number.exec(text);if(!m)fail();const v=m[0];i=number.lastIndex;
  if(v.length>128)throw Error('SOURCE_NUMBER_OUT_OF_RANGE');
  if(/^-?\d+$/.test(v)){if(v.replace('-','').length>16)throw Error('SOURCE_NUMBER_OUT_OF_RANGE');const x=BigInt(v);if(x>9007199254740991n||x< -9007199254740991n)throw Error('SOURCE_NUMBER_OUT_OF_RANGE');}
  else if((Number(v)===0&&/[1-9]/.test(v.split(/[eE]/)[0]))||!Number.isFinite(Number(v))||Math.abs(Number(v))>Number.MAX_SAFE_INTEGER||v.split(/[eE]/)[0].replace(/[-.]/g,'').replace(/^0+/,'').length>15)throw Error('SOURCE_NUMBER_OUT_OF_RANGE');
 }
 value(0);space();if(i!==n)fail();try{return JSON.parse(text);}catch{fail();}
}
