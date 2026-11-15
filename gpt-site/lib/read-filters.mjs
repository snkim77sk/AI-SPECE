import {canonicalRegion} from './g2b-domain.mjs';
// Read groups preserve original source names. They are not matching evidence.
export function regionHistoryMembers(value){const selected=canonicalRegion(value)||String(value||'').trim();return selected==='전남광주통합특별시'?['전남광주통합특별시','광주광역시','전라남도']:[selected];}
export function regionWhere(value,where,args,column='region'){if(!value)return;const values=regionHistoryMembers(value);where.push(`${column} IN (${values.map(()=>'?').join(',')})`);args.push(...values);}
export function regionChoices(values){const out=new Set(values);if(out.has('광주광역시')||out.has('전라남도'))out.add('전남광주통합특별시');return [...out].sort();}
export const INCHEON_SCOPES=[
 ['INCHEON_ALL','인천광역시 전체',[],[]],
 ['INCHEON_CITY','인천광역시 본청',['인천광역시','인천광역시청','인천광역시 본청'],[]],
 ['INCHEON_GENERAL_CONSTRUCTION','종합건설본부',[],['종합건설본부']],
 ['INCHEON_IFEZ','인천경제자유구역청',[],['경제자유구역청','인천경제자유구역청']],
 ['INCHEON_WATERWORKS','상수도사업본부',[],['상수도사업본부']],
 ['INCHEON_URBAN_RAIL','도시철도건설본부',[],['도시철도건설본부']],
 ...[['GANGHWA','강화군'],['ONGJIN','옹진군'],['JEMULPO','제물포구'],['YEONGJONG','영종구'],['MICHEOHOL','미추홀구'],['YEONSU','연수구'],['NAMDONG','남동구'],['BUPYEONG','부평구'],['GYEYANG','계양구'],['SEOHAE','서해구'],['GEOMDAN','검단구']].map(([key,label])=>['INCHEON_'+key,label,[],[label]])
];
const norm=value=>String(value).toLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
// Bounded SQL expression removes ordinary source punctuation/whitespace. Unusual
// Unicode punctuation is not guessed or rewritten into another institution.
function compactSQL(field){let sql=`lower(COALESCE(${field},''))`;for(const char of [' ','.',',','-','_','(',')','[',']','{','}',':',';','/','·','ㆍ'])sql=`replace(${sql},'${char}','')`;for(const code of [9,10,13,160,12288])sql=`replace(${sql},char(${code}),'')`;return sql;}
export function incheonScopeWhere(params,where,args,institutionField){const code=params.get('incheonScope');if(!code)return;const spec=INCHEON_SCOPES.find(row=>row[0]===code);if(!spec)throw Error('INVALID_INCHEON_SCOPE');if(params.get('region')&&regionHistoryMembers(params.get('region'))[0]!=='인천광역시')throw Error('INCHEON_SCOPE_REGION_CONFLICT');where.push("region='인천광역시'");if(code==='INCHEON_ALL')return;const clauses=[];for(const field of ['org','dept',institutionField]){const expression=compactSQL(field);for(const term of spec[2]){clauses.push(`${expression}=?`);args.push(norm(term));}for(const term of spec[3]){clauses.push(`instr(${expression},?)>0`);args.push(norm(term));}}where.push('('+clauses.join(' OR ')+')');}
