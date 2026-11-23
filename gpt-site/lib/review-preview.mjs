import {LIGHTING_CODES,POLE_CODES,SMART_CONTROL_CODE} from './g2b-domain.mjs';

export const REVIEW_MAX_BYTES=2*1024*1024;
export const REVIEW_MAX_ROWS=2500;
const text=x=>x===null||x===undefined?'':String(x).trim();
const pick=(record,aliases)=>{for(const alias of aliases){const value=text(record[alias]);if(value!=='')return value;}return '';};
const money=value=>{const v=text(value).replace(/[,\s원₩]/g,'');return /^-?\d+(?:\.\d+)?$/.test(v)?v:null;};
const sourceDate=value=>{
 const v=text(value).replace(/T.*$/,'').replace(/[./]/g,'-');
 const result=/^\d{8}$/.test(v)?v.slice(0,4)+'-'+v.slice(4,6)+'-'+v.slice(6,8):v;
 if(!/^\d{4}-\d{2}-\d{2}$/.test(result))return '';
 const d=new Date(result+'T00:00:00Z');return !Number.isNaN(d.getTime())&&d.toISOString().slice(0,10)===result?result:'';
};
const contractId=['납품요구번호','요구번호','dlvrReqNo','deliveryReqNo','request_no','계약번호','cntrctNo','contractNo','contract_no'];
const projectName=['사업명','세부사업명','프로젝트명','project','dbiz_nm','사업내용'];
const contractAgency=['수요기관','수요기관명','수요처','발주기관','기관명','dminsttNm','agency','demandOrgName','org'];
const budgetAgency=['기관명','수요기관','수요기관명','org','inst_nm','institution_name'];
const itemName=['품명','물품명','품목명','제품명','상품명','item_name','prdctIdntNoNm','prdctNm','detail_name'];
const budgetNumber=['예산액','사업예산','예산금액','총예산','budget_amount'];
const unit=['단위','금액단위','표시단위','unit','amount_unit'];

function firstLineDelimiter(src) {
 let quote=false,commas=0,tabs=0;
 for(let i=0;i<src.length;i++){
  const c=src[i];if(c==='"'&&src[i+1]==='"'&&quote){i++;continue;}
  if(c==='"'){quote=!quote;continue;}
  if(!quote&&(c==='\r'||c==='\n'))break;
  if(!quote&&c===',')commas++;
  if(!quote&&c==='\t')tabs++;
 }
 return tabs>commas?'\t':',';
}

function parseCsv(src){
 const delimiter=firstLineDelimiter(src),grid=[],row=[];let value='',quoted=false;
 const cell=()=>{if(value.length>12000)throw Error('REVIEW_FIELD_TOO_LONG');row.push(value);value='';if(row.length>100)throw Error('REVIEW_TOO_MANY_COLUMNS');};
 const finish=()=>{cell();if(row.some(v=>v.trim()!=='')){grid.push(row.splice(0));if(grid.length>REVIEW_MAX_ROWS+1)throw Error('REVIEW_TOO_MANY_ROWS');}else row.length=0;};
 for(let i=0;i<src.length;i++){
  const c=src[i];
  if(quoted){if(c==='"'){if(src[i+1]==='"'){value+='"';i++;}else quoted=false;}else value+=c;}
  else if(c==='"'){if(value!=='')throw Error('REVIEW_INVALID_CSV');quoted=true;}
  else if(c===delimiter)cell();
  else if(c==='\n'||c==='\r'){if(c==='\r'&&src[i+1]==='\n')i++;finish();}
  else value+=c;
  if(value.length>12000)throw Error('REVIEW_FIELD_TOO_LONG');
 }
 if(quoted)throw Error('REVIEW_INVALID_CSV');
 if(value!==''||row.length)finish();
 if(grid.length<2)throw Error('REVIEW_NO_ROWS');
 const headings=grid.shift().map(h=>h.trim().replace(/^\uFEFF/,''));
 if(headings.some(h=>!h)||new Set(headings).size!==headings.length)throw Error('REVIEW_INVALID_HEADERS');
 return grid.map(cols=>{const result=Object.create(null);headings.forEach((h,i)=>{result[h]=cols[i]||'';});return result;});
}

function readRows(source){
 const input=source.trim().replace(/^\uFEFF/,'');
 if(!input)throw Error('REVIEW_EMPTY_INPUT');
 if(input.startsWith('[')||input.startsWith('{')){
  let payload;try{payload=JSON.parse(input);}catch{throw Error('REVIEW_INVALID_JSON');}
  const candidate=Array.isArray(payload)?payload:payload?.rows??payload?.items??payload?.response?.body?.items?.item;
  const rows=Array.isArray(candidate)?candidate:candidate&&typeof candidate==='object'?[candidate]:null;
  if(!rows)throw Error('REVIEW_INVALID_JSON_ROWS');
  if(rows.length>REVIEW_MAX_ROWS)throw Error('REVIEW_TOO_MANY_ROWS');
  if(rows.length===0)throw Error('REVIEW_NO_ROWS');
  return {rows,format:'JSON'};
 }
 return {rows:parseCsv(input.replace(/^sep=(,|\t)\r?\n/i,'')),format:'CSV/TSV'};
}
function plainRecord(x){
 if(!x||typeof x!=='object'||Array.isArray(x)||Object.keys(x).length>100)return false;
 return Object.values(x).every(v=>v===null||['string','number','boolean'].includes(typeof v)&&String(v).length<=12000);
}
function normalizeContract(r){
 const request_no=pick(r,contractId),contract_no=pick(r,['계약번호','cntrctNo','contractNo','contract_no']);
 if(!request_no)return null;
 const change_order=pick(r,['변경차수','납품요구변경차수','dlvrReqChgOrd','change_order'])||'0';
 const sequence=pick(r,['품목순번','품목일련번호','prdctSno','sequence','seq']);
 const item_id=pick(r,['식별번호','물품식별번호','품목식별번호','prdctIdntNo','item_id','itemId']);
 const agency=pick(r,contractAgency);
 const vendor=pick(r,['계약업체','업체명','공급업체','corpNm','vendor','cntrctCorpNm']);
 const item_name=pick(r,itemName);
 const model=pick(r,['모델명','규격','model','modelNm','specNm']);
 const detail_code=pick(r,['세부품명번호','세부품목분류번호','dtilPrdctClsfcNo','detail_code','prdctClsfcNo']).replace(/\D/g,'');
 const isLighting=LIGHTING_CODES.has(detail_code)||POLE_CODES.has(detail_code)||detail_code===SMART_CONTROL_CODE||/(LED|엘이디|조명|등기구|다운라이트|투광등|보안등|가로등|등주)/i.test(item_name+' '+model);
 const institution=/한국토지주택공사|(^|[^A-Za-z])LH([^A-Za-z]|$)/i.test(agency)?'LH':/서울주택도시공사|(^|[^A-Za-z])SH([^A-Za-z]|$)/i.test(agency)?'SH':'';
 const source_date=sourceDate(pick(r,['납품요구일자','계약일자','계약일','날짜','dlvrReqRcptDate','source_date','cntrctDate']));
 const amount=money(pick(r,['납품금액','계약금액','공급가액','금액','prdctAmt','dlvrReqDtlAmt','amount','dlvrReqAmt']));
 const identityPart=sequence||item_id;
 const duplicateKey=identityPart?JSON.stringify([request_no,change_order,sequence,item_id]):null;
 return {request_no,contract_no,change_order,sequence,item_id,source_date,agency,vendor,item_name,model,detail_code,amount,isLighting,institution,duplicateKey};
}
function normalizeBudget(r){
 const project=pick(r,projectName);
 if(!project)return null;
 const fiscal_year=pick(r,['회계연도','연도','fyr','fiscal_year','year']);
 const org=pick(r,budgetAgency),dept=pick(r,['부서','담당부서','dept','dept_nm']);
 const service=pick(r,['원천','source','service'])||'미확인';
 const snapshot_date=sourceDate(pick(r,['기준일','집행기준일','snapshot_date','exe_ymd','조회일자']));
 const budgetColumn=Object.keys(r).find(h=>/^(예산액|사업예산|예산금액|총예산)\s*\((천원|백만원|원)\)$/.test(h));
 const appropriationColumn=Object.keys(r).find(h=>/^(예산편성액|편성액)\s*\((천원|백만원|원)\)$/.test(h));
 const budget_amount=money(pick(r,budgetNumber)|| (budgetColumn?text(r[budgetColumn]):'')),appropriation_amount=money(pick(r,['appropriation_amount','편성액','예산편성액'])||(appropriationColumn?text(r[appropriationColumn]):'')),executed_amount=money(pick(r,['집행액','지출액','executed_amount']));
 const remaining_amount=money(pick(r,['잔액','집행잔액','예산잔액','remaining_amount']));
 const category=pick(r,['분류','category']);
 const item=pick(r,['사업유형','사업내용','세부사업명','project']);
 const isLighting=/(LED|엘이디|조명|등주|등기구|가로등|보안등|경관등)/i.test(project+' '+category+' '+item);
 const rawUnit=pick(r,unit);
 const units=[...new Set(Object.keys(r).map(h=>h.match(/\((천원|백만원|원)\)/)?.[1]).filter(Boolean))];
 const amount_unit=units.length>1||rawUnit&&units.length&&units.some(x=>x!==rawUnit)?'혼합·확인 필요':rawUnit||units[0]||'미확인';
 return {project,fiscal_year,org,dept,service,snapshot_date,budget_amount,appropriation_amount,executed_amount,remaining_amount,category,amount_unit,isLighting};
}
export function parseReviewInput(contents,kind='contract'){
 if(kind!=='contract'&&kind!=='budget')throw Error('REVIEW_INVALID_KIND');
 if(typeof contents!=='string'||new TextEncoder().encode(contents).length>REVIEW_MAX_BYTES)throw Error('REVIEW_FILE_TOO_LARGE');
 const {rows,format}=readRows(contents),seen=new Set(),result=[];
 let excluded=0,duplicates=0;
 for(const record of rows){
  if(!plainRecord(record)){excluded++;continue;}
  const row=kind==='contract'?normalizeContract(record):normalizeBudget(record);
  if(!row){excluded++;continue;}
  if(kind==='contract'&&row.duplicateKey){if(seen.has(row.duplicateKey)){duplicates++;continue;}seen.add(row.duplicateKey);}
  result.push(row);
 }
 return {kind,format,total:rows.length,excluded,duplicates,rows:result,ephemeral:true};
}
export function moneyLabel(value){const v=text(value);if(!/^-?\d+(?:\.\d+)?$/.test(v))return '미확인';const [whole,decimal]=v.split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(decimal?'.'+decimal:'');}
const csvCell=value=>{let valueText=text(value);if(/^[=+@\t\r\n]/.test(valueText)||(/^-/).test(valueText)&&!/^-\d+(?:\.\d+)?$/.test(valueText))valueText="'"+valueText;return '"'+valueText.replace(/"/g,'""')+'"';};
export function reviewCsv(rows,kind){
 if(kind!=='contract'&&kind!=='budget')throw Error('REVIEW_INVALID_KIND');
 const fields=kind==='contract'?['source_date','request_no','contract_no','agency','vendor','item_id','item_name','model','amount','institution','isLighting']:['fiscal_year','snapshot_date','org','dept','project','service','budget_amount','appropriation_amount','executed_amount','remaining_amount','amount_unit','isLighting'];
 const header=fields.map(csvCell).join(',');
 return '\uFEFF'+[header,...rows.map(r=>fields.map(field=>csvCell(r[field])).join(','))].join('\r\n');
}
