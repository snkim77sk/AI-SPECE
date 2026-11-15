const LIST_KEYS=['mode','service','year','q','preset_q','category','view','sort','execution','region','org','dept','orgKey','deptKey','incheonScope'];
/** Never accept an arbitrary return URL, action, cursor, or a different data mode. */
export function projectContext(search=''){
 const p=new URLSearchParams(search),mode='live',service=p.get('service')==='AIDFA'?'AIDFA':'QWGJK';
 const raw=p.get('returnQuery')||'',back=new URLSearchParams();
 if(raw.length<=8192){const source=new URLSearchParams(raw);for(const key of LIST_KEYS){const value=source.get(key);if(value!==null)back.set(key,value);}}
 back.set('mode',mode);back.set('service',service);
 return{mode,service,request:new URLSearchParams({mode,service,key:p.get('key')||''}),listHref:'/budget?'+back,matchingHref:'/matching?mode='+mode};
}
export function projectHref(mode,service,key,returnQuery=''){
 return '/budget/project?'+new URLSearchParams({mode:'live',service:service==='AIDFA'?'AIDFA':'QWGJK',key,returnQuery});
}
