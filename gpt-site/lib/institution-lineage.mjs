import {regionHistoryMembers} from './read-filters.mjs';
import {canonicalRegion} from './g2b-domain.mjs';
import {norm} from './matching.mjs';
// Versioned configured administrative relations. Never changes source records.
// Parent-city / ward affinity is NOT a successor identity.
export const LINEAGE_VERSION='institution-lineage-v1-conservative';
export const LINEAGE_BASES=Object.freeze(['SOURCE_ORG','CURRENT_ORG','INCHON_DONGGU_TO_JEMULPO','INCHON_JUNGGU_TO_JEMULPO','INCHON_JUNGGU_TO_YEONGJONG','INCHON_SEOGU_TO_SEOHAE','INCHON_SEOGU_TO_GEOMDAN','JEONNAM_GWANGJU_TOP_LEVEL_MERGE','AMBIGUOUS_SPLIT_RETAINED','POST_EFFECTIVE_PREDECESSOR_RETAINED','HWASEONG_PARENT_RETAINED']);
const splits=[
 ['중구','제물포구','INCHON_JUNGGU_TO_JEMULPO',['신포동','연안동','신흥동','도원동','율목동','동인천동','개항동']],
 ['중구','영종구','INCHON_JUNGGU_TO_YEONGJONG',['영종동','영종1동','영종2동','운서동','운서1동','운서2동','용유동']],
 ['서구','서해구','INCHON_SEOGU_TO_SEOHAE',['검암경서동','연희동','청라1동','청라2동','청라3동','가정1동','가정2동','가정3동','신현원창동','석남1동','석남2동','석남3동','가좌1동','가좌2동','가좌3동','가좌4동']],
 ['서구','검단구','INCHON_SEOGU_TO_GEOMDAN',['검단동','불로대곡동','원당동','당하동','오류왕길동','마전동','아라1동','아라2동']]
];
export function lineageDay(value){if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))throw Error('LINEAGE_DATE_REQUIRED');const n=Date.parse(value+'T00:00:00Z');if(!Number.isFinite(n)||new Date(n).toISOString().slice(0,10)!==value)throw Error('LINEAGE_DATE_INVALID');return value;}
function localOrganization(name,region){let value=norm(name).replace(/청$/,'');for(const prefix of region==='인천광역시'?['인천광역시','인천']:region==='경기도'?['경기도','경기']:[])if(value.startsWith(prefix)){value=value.slice(prefix.length);break;}return value;}
export function institutionLineage({org,region,projectText='',sourceDate,asOfDate=sourceDate}={}){
 for(const[value,max]of[[org,1024],[region,256],[projectText,65536]])if(typeof value!=='string'||new TextEncoder().encode(value).byteLength>max||value.includes('\0'))throw Error('LINEAGE_TEXT_INVALID');
 const source=lineageDay(sourceDate),asOf=lineageDay(asOfDate),r=canonicalRegion(region),known=canonicalRegion(org);if(r&&known&&r!==known)throw Error('LINEAGE_REGION_CONFLICT');
 const result={version:LINEAGE_VERSION,org_name:org.trim(),region_key:r,org_key:norm(org),source_org_name:org.trim(),source_region_name:r,applied:false,basis:'SOURCE_ORG',as_of_date:asOf,unresolved:false};
 const mapped=(name,region,basis)=>({...result,org_name:name,region_key:region,org_key:norm(name),applied:true,basis});
 if(r==='인천광역시'){
  const local=localOrganization(org,r),effective='2026-07-01';
  if(['제물포구','영종구','서해구','검단구'].includes(local)&&asOf>=effective)return{...mapped('인천광역시 '+local,r,'CURRENT_ORG'),applied:norm(org)!==norm('인천광역시 '+local)};
  if(['중구','동구','서구'].includes(local)&&asOf>=effective){
   if(source>=effective)return{...result,basis:'POST_EFFECTIVE_PREDECESSOR_RETAINED',unresolved:true};
   if(local==='동구')return mapped('인천광역시 제물포구',r,'INCHON_DONGGU_TO_JEMULPO');
   // Exact locality tokens: substring coincidences and mixed successors cannot merge.
   const tokens=new Set(projectText.split(/[\s,;:()\[\]\/]+/).map(norm)),matches=splits.filter(([old,,,places])=>old===local&&places.some(p=>tokens.has(norm(p))));
   if(matches.length===1)return mapped('인천광역시 '+matches[0][1],r,matches[0][2]);
   return{...result,basis:'AMBIGUOUS_SPLIT_RETAINED',unresolved:true};
  }
 }
 if(r==='경기도'&&localOrganization(org,r)==='화성시')return{...result,basis:'HWASEONG_PARENT_RETAINED'};
 const top=norm(org).replace(/청$/,'');
 if(top==='전남광주통합특별시'&&r==='전남광주통합특별시'&&asOf>='2026-07-01')return{...mapped('전남광주통합특별시',r,'CURRENT_ORG'),applied:norm(org)!==top};
 if(((top==='광주광역시'&&r==='광주광역시')||(top==='전라남도'&&r==='전라남도'))&&asOf>='2026-07-01'){
  if(source<'2026-07-01')return mapped('전남광주통합특별시','전남광주통합특별시','JEONNAM_GWANGJU_TOP_LEVEL_MERGE');
  return{...result,basis:'POST_EFFECTIVE_PREDECESSOR_RETAINED',unresolved:true};
 }
 return result;
}

// The read filter includes original source regions after an exact top-level merge.
// This is selection metadata, not permission to merge arbitrary municipalities.
export function patternRegionWhere(value,where,args){if(!value)return;const members=regionHistoryMembers(value),marks=members.map(()=>'?').join(',');where.push(`(region_key IN (${marks}) OR EXISTS(SELECT 1 FROM json_each(row_json,'$.historical_region_names') WHERE value IN (${marks})))`);args.push(...members,...members);}
