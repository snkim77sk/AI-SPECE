// Verified relationships are candidate evidence, never source-name rewrites.
export const ADMIN_SOURCES={incheon:'https://www.incheon.go.kr/IC040102',incheonLaw:'https://www.law.go.kr/LSW/lsInfoR.do?efYd=20260701&lsiSeq=286453&urlMode=lsInfoR&viewCls=lsInfoR',hwaseong:'https://www.hscity.go.kr/office/intro.jsp',hwaseongDate:'https://www.hscity.go.kr/www/user/bbs/BD_selectBbs.do?q_bbsCode=1019&q_bbscttSn=20260106145941632',mergedLaw:'https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=284111'};
const norm=v=>String(v??'').toLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
const regions={인천:'인천',인천광역시:'인천',경기:'경기',경기도:'경기',광주:'광주',광주광역시:'광주',전남:'전남',전라남도:'전남',전남광주:'전남광주',전남광주통합특별시:'전남광주'};
const region=v=>regions[norm(v)]||'';
function org(name,top){let n=norm(name).replace(/청$/,'');const prefixes=top==='인천'?['인천광역시','인천']:top==='경기'?['경기도','경기']:[];for(const p of prefixes)if(n.startsWith(p)){n=n.slice(p.length);break;}return n;}
function exactDay(v){const s=String(v??'').slice(0,10),n=Date.parse(s+'T00:00:00Z');return /^\d{4}-\d{2}-\d{2}$/.test(s)&&Number.isFinite(n)&&new Date(n).toISOString().slice(0,10)===s?s:null;}
function ordered(oldDate,newDate,effective){const a=exactDay(oldDate),b=exactDay(newDate);return a&&b&&a<effective&&b>=effective;}
const incheon=[
 ['중구','제물포구',['신포동','연안동','신흥동','도원동','율목동','동인천동','개항동']],
 ['동구','제물포구',['만석동','화수1·화평동','화수2동','송현1·2동','송현3동','송림1동','송림2동','송림3·5동','송림4동','송림6동','금창동']],
 ['중구','영종구',['영종동','영종1동','영종2동','운서1동','운서2동','용유동']],
 ['서구','서해구',['검암경서동','연희동','청라1동','청라2동','청라3동','가정1동','가정2동','가정3동','신현원창동','석남1동','석남2동','석남3동','가좌1동','가좌2동','가좌3동','가좌4동']],
 ['서구','검단구',['검단동','불로대곡동','원당동','당하동','오류왕길동','마전동','아라1동','아라2동']]
];
// Exact token clues intentionally prefer false negatives over location substring guesses.
function clue(a,b,values){const tokens=x=>new Set(String(x??'').split(/[\s,;:()\[\]\/]+/).map(norm));const x=tokens(a),y=tokens(b);return values.find(v=>x.has(norm(v))&&y.has(norm(v)))||'';}
const wards=['만세구','효행구','병점구','동탄구'];
export const ADMIN_IDENTITY_CLUES=[...new Set([...incheon.flatMap(([a,b,places])=>[a,b,...places]),...wards,'화성시','전라남도','광주광역시','전남광주통합특별시'])];
function topIdentity(name){const n=norm(name).replace(/청$/,'');return ['광주광역시','전라남도','전남광주통합특별시'].includes(n)?n:'';}
export function adminCandidateKeys(name,sourceRegion){const keys=new Set(),r=region(sourceRegion),n=org(name,r);if(r==='인천'){for(const [old,next]of incheon)if(n===old||n===next)keys.add('admin-family:incheon:'+old);}
 if(r==='경기'&&(n==='화성시'||wards.some(w=>n==='화성시'+w||n===w)))keys.add('admin-family:hwaseong:wards');
 const top=topIdentity(name);if(top&&((top==='광주광역시'&&r==='광주')||(top==='전라남도'&&r==='전남')||(top==='전남광주통합특별시'&&r==='전남광주')))keys.add('admin-family:jeonnam-gwangju');return keys;}
export function administrativeRelation(left,right){for(const [old,next]of[[left,right],[right,left]]){const ro=region(old.region),rn=region(next.region);if(ro==='인천'&&rn==='인천'&&ordered(old.date,next.date,'2026-07-01')){const a=org(old.org,ro),b=org(next.org,rn);for(const [before,after,localities]of incheon){if(a!==before||b!==after)continue;const place=clue(old.text,next.text,localities);if(place){const mixed=incheon.filter(([parent,child])=>parent===before&&child!==after).some(([,,places])=>clue(old.text,old.text,places)||clue(next.text,next.text,places));return{basis:'ADMIN_TRANSITION_ORG_MATCH',identityClues:ADMIN_IDENTITY_CLUES,shared:['incheon:'+before+'>'+after],evidence:['EFFECTIVE:2026-07-01','SHARED_LOCALITY:'+place,...(mixed?['MIXED_SUCCESSOR_LOCALITY_SCOPE']:[]),'SOURCE:'+ADMIN_SOURCES.incheon],capped:mixed};}}}
 if(ro==='경기'&&rn==='경기'&&ordered(old.date,next.date,'2026-02-01')&&org(old.org,ro)==='화성시'){const nextOrg=org(next.org,rn),ward=wards.find(w=>nextOrg==='화성시'+w||nextOrg===w);if(ward&&clue(old.text,next.text,[ward]))return{basis:'ADMIN_PARENT_WARD_SCOPE_CANDIDATE',identityClues:ADMIN_IDENTITY_CLUES,shared:['hwaseong:'+ward],evidence:['EFFECTIVE:2026-02-01','SHARED_WARD:'+ward,'PARENT_CITY_STILL_EXISTS_NOT_SUCCESSOR_PROOF','SOURCE:'+ADMIN_SOURCES.hwaseong],capped:true};}
 const a=topIdentity(old.org),b=topIdentity(next.org);if(ordered(old.date,next.date,'2026-07-01')&&b==='전남광주통합특별시'&&rn==='전남광주'&&((a==='광주광역시'&&ro==='광주')||(a==='전라남도'&&ro==='전남')))return{basis:'ADMIN_TOP_LEVEL_MERGE_MATCH',shared:['jeonnam-gwangju:'+a],evidence:['EFFECTIVE:2026-07-01','EXACT_TOP_LEVEL_GOVERNMENTS_ONLY','SOURCE:'+ADMIN_SOURCES.mergedLaw],capped:false};}
 return null;}
