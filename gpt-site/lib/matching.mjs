import {adminCandidateKeys,administrativeRelation,ADMIN_IDENTITY_CLUES} from './admin-history.mjs';
// Pure candidate port. No storage, network, environment access, or external dependencies.
import { _GENERIC_TOKENS, _GENERIC_IDENTITY_TOKENS, _SIGNAL_TERMS, LOCALITY_RENAMES } from './matching-constants.mjs';
const generic = new Set(_GENERIC_TOKENS), identityGeneric = new Set(_GENERIC_IDENTITY_TOKENS);
const text = v => String(v ?? '').trim();
export const norm = v => text(v).toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const compare = (a, b) => { for (let i = 0; i < a.length; i++) {
    const c = cmp(a[i], b[i]);
    if (c)
        return c;
} return 0; };
export function integer(v, { missing = 0n } = {}) { if (v === null || v === undefined || text(v) === '')
    return missing; if (typeof v === 'number' && !Number.isSafeInteger(v))
    throw Error('UNSAFE_INTEGER'); const s = text(v).replaceAll(',', ''); if (!/^-?\d+$/.test(s))
    throw Error('INVALID_INTEGER'); return BigInt(s); }
const change = v => BigInt(text(v).replace(/\D/g, '') || '0');
const latestRank = r => [change(r.delivery_change_order), text(r.is_final_delivery_request).toUpperCase() === 'Y' ? 1 : 0, text(r.source_date), text(r.fetched_at), text(r.source_key)];
export function latestDetails(rows) { const m = new Map(); for (const r of rows) {
    if (!text(r.source_key))
        throw Error('SOURCE_KEY_REQUIRED');
    const key = JSON.stringify(text(r.delivery_req_no) && text(r.detail_seq) ? ['ITEM', text(r.delivery_req_no), text(r.detail_seq)] : ['SOURCE', text(r.source_key)]);
    const old = m.get(key);
    if (!old || compare(latestRank(r), latestRank(old)) > 0)
        m.set(key, r);
} return [...m.values()]; }
const distinct = (rows, f, digits = false) => new Map(rows.map(r => [digits ? text(r[f]).replace(/\D/g, '') : norm(r[f]), text(r[f])]).filter(([k]) => k));
export function integrityIssues(rows) { const out = []; if (distinct(rows, 'demand_org').size > 1)
    out.push('DEMAND_ORG_CONFLICT'); const biz = distinct(rows, 'vendor_bizno', true); if (biz.size > 1)
    out.push('VENDOR_BIZNO_CONFLICT');
else if (!biz.size && distinct(rows, 'vendor_name').size > 1)
    out.push('VENDOR_NAME_CONFLICT'); if (distinct(rows, 'contract_no').size > 1)
    out.push('CONTRACT_NO_CONFLICT'); return out.sort(); }
export function sumDecimals(values) { let total = 0n, scale = 0; for (const v of values) {
    if (v === null || v === undefined || text(v) === '')
        continue;
    if (typeof v === 'number' && !Number.isSafeInteger(v))
        throw Error('DECIMAL_STRING_REQUIRED');
    const s = text(v).replaceAll(',', '');
    if (!/^-?\d+(\.\d+)?$/.test(s))
        throw Error('INVALID_DECIMAL');
    const digits = (s.split('.')[1] || '').length;
    const n = BigInt(s.replace('.', ''));
    if (digits > scale) {
        total *= 10n ** BigInt(digits - scale);
        scale = digits;
    }
    total += n * 10n ** BigInt(scale - digits);
} const sign = total < 0n ? '-' : ''; let s = (total < 0n ? -total : total).toString().padStart(scale + 1, '0'); return scale ? sign + (s.slice(0, -scale) + '.' + s.slice(-scale)).replace(/\.?0+$/, '') : sign + s; }
export function groupRequests(rows, { alreadyLatest = false } = {}) { const groups = new Map(); for (const r of alreadyLatest ? rows : latestDetails(rows)) {
    if (!text(r.source_key))
        throw Error('SOURCE_KEY_REQUIRED');
    const key = JSON.stringify(text(r.delivery_req_no) ? ['REQUEST', text(r.delivery_req_no)] : ['SOURCE', text(r.source_key)]);
    if (!groups.has(key))
        groups.set(key, []);
    groups.get(key).push(r);
} const out = []; for (const rs of groups.values()) {
    const rep = rs.reduce((a, b) => compare(latestRank(a), latestRank(b)) >= 0 ? a : b);
    const req = text(rep.delivery_req_no), issues = req ? integrityIssues(rs) : [];
    const cats = [...new Set(rs.map(r => text(r.primary_category).toUpperCase()).filter(c => ['LIGHTING', 'POLE'].includes(c)))].sort();
    const dates = rs.map(r => text(r.source_date)).filter(Boolean).sort();
    const item = { ...rep, source_key: req ? 'REQUEST:' + req : rep.source_key, delivery_req_no: req, source_date: dates[0] || '', request_integrity_valid: !issues.length, request_integrity_status: issues.length ? 'EXCLUDED_CONFLICT' : 'VALID', request_integrity_issues: issues, primary_categories: cats, primary_category: cats.length === 1 ? cats[0] : 'MIXED_TARGET', detail_seq: '', amount: rs.reduce((s, r) => s + integer(r.amount), 0n).toString(), quantity: sumDecimals(rs.map(r => r.quantity)), request_detail_rows: rs.length, amount_basis: 'SUM_LATEST_TARGET_DETAIL_ITEM_AMOUNT', unit_price: '0', unit_price_basis: 'REQUEST_AGGREGATE_NOT_APPLICABLE', delivery_req_total_amount: rs.reduce((m, r) => { const v = integer(r.delivery_req_total_amount); return v > m ? v : m; }, 0n).toString() };
    for (const f of ['detail_item_name', 'item_name', 'model_name', 'vendor_name'])
        item[f] = [...new Set(rs.map(r => text(r[f])).filter(Boolean))].sort().join(' / ');
    for (const f of ['demand_org', 'vendor_bizno', 'contract_no']) {
        const vals = distinct(rs, f, f === 'vendor_bizno');
        if (vals.size === 1)
            item[f] = [...vals.values()][0];
    }
    out.push(item);
} return out.sort((a, b) => compare([b.source_date, b.delivery_req_no, b.source_key], [a.source_date, a.delivery_req_no, a.source_key])); }
export function orgAliases(name, region = '') { const t = norm(name); if (!t)
    return new Set(); const aliases = new Set([t]), tokens = ['특별자치시', '특별자치도', '광역시', '특별시']; for (const token of tokens)
    if (t.includes(token))
        aliases.add(t.replaceAll(token, '')); const full = norm(region); if (full) {
    let short = full;
    for (const token of [...tokens, '도'])
        short = short.replaceAll(token, '');
    const top = new Set([full]);
    if (short) {
        top.add(short);
        if (/(특별자치시|광역시|특별시)$/.test(full))
            top.add(short + '시');
    }
    if (top.has(t))
        for (const x of top)
            aliases.add(x);
    for (const x of [...aliases]) {
        if (x.startsWith(full) && x.length > full.length)
            aliases.add(x.slice(full.length));
        else if (short && x.startsWith(short) && x.length > short.length && x.slice(short.length).length >= 2)
            aliases.add(x.slice(short.length));
    }
} return new Set([...aliases].filter(x => x.length >= 2)); }
export function orgCandidateKeys(name,region=''){if(!regionKey(region))return new Set();return new Set([...orgAliases(name,region),...adminCandidateKeys(name,region)]);}
const REGION_NAMES={서울특별시:'서울',서울:'서울',부산광역시:'부산',부산:'부산',대구광역시:'대구',대구:'대구',인천광역시:'인천',인천:'인천',대전광역시:'대전',대전:'대전',울산광역시:'울산',울산:'울산',세종특별자치시:'세종',세종:'세종',경기도:'경기',경기:'경기',강원특별자치도:'강원',강원도:'강원',강원:'강원',충청북도:'충북',충북:'충북',충청남도:'충남',충남:'충남',전북특별자치도:'전북',전라북도:'전북',전북:'전북',광주광역시:'광주',광주:'광주',전라남도:'전남',전남:'전남',전남광주통합특별시:'전남광주',전남광주:'전남광주',경상북도:'경북',경북:'경북',경상남도:'경남',경남:'경남',제주특별자치도:'제주',제주도:'제주',제주:'제주'};
const REGION_PREFIXES=Object.entries(REGION_NAMES).filter(([k])=>k.length>2).sort((a,b)=>b[0].length-a[0].length);
const explicitOrgRegion=v=>REGION_PREFIXES.find(([k])=>norm(v).startsWith(k))?.[1]||'';
const regionKey=v=>REGION_NAMES[norm(v)]||explicitOrgRegion(v);
const ADMIN_IDENTITY_TOKENS=new Set(ADMIN_IDENTITY_CLUES.flatMap(v=>[...tokenForms(v).strong,...tokenForms(v).weak]));
export function organizationBasis(b, s) { if(!regionKey(b.region_name)||!regionKey(s.demand_region))return {basis:null,shared:[],unsupported:['VERIFIED_REGION_REQUIRED']}; if((explicitOrgRegion(b.org_name)&&explicitOrgRegion(b.org_name)!==regionKey(b.region_name))||(explicitOrgRegion(s.demand_org)&&explicitOrgRegion(s.demand_org)!==regionKey(s.demand_region)))return{basis:null,shared:[],unsupported:['ORGANIZATION_REGION_CONFLICT']}; const transition=administrativeRelation({org:b.org_name,region:b.region_name,date:b.source_date||b.snapshot_date,text:[b.project_name,b.field_name,b.section_name,b.account_name].filter(Boolean).join(' ')},{org:s.demand_org,region:s.demand_region,date:s.source_date,text:[s.delivery_req_name,s.detail_item_name,s.item_name,s.model_name].filter(Boolean).join(' ')});if(transition)return {...transition,unsupported:[]}; if (regionKey(b.region_name) && regionKey(s.demand_region) && regionKey(b.region_name) !== regionKey(s.demand_region))
    return { basis: null, shared: [], unsupported: ['CROSS_REGION_OR_TRANSITION_NOT_SUPPORTED'] }; const a = orgAliases(b.org_name, b.region_name), z = orgAliases(s.demand_org, s.demand_region); const shared = [...a].filter(x => z.has(x)).sort(); if (!shared.length)
    return { basis: null, shared, unsupported: ['ADMIN_TRANSITION_NOT_IMPLEMENTED'] }; return { basis: norm(b.org_name) === norm(s.demand_org) ? 'EXACT_ORG_NAME' : 'ORG_ALIAS_MATCH', shared, unsupported: [] }; }
export function tokenForms(value) { const strong = new Set(), weak = new Set(); for (const raw of text(value).toLowerCase().match(/[0-9a-z가-힣]+/g) || []) {
    if (raw.length < 2 || generic.has(raw) || /^20\d\d$/.test(raw))
        continue;
    const t = norm(raw);
    if (identityGeneric.has(t) || /^[0-9a-z]+$/.test(t) || /^\d+(공구|구역|지구|구간)$/.test(t))
        continue;
    if (LOCALITY_RENAMES[t])
        strong.add(LOCALITY_RENAMES[t][0]);
    const suffix = /(읍|면|동|리|구)$/.test(t), stem = suffix ? t.slice(0, -1) : t;
    if (stem.length >= 2) {
        strong.add('loc:' + stem);
        const base = stem.replace(/\d+$/, '');
        if (suffix && base !== stem && base.length >= 2)
            weak.add('loc:' + base);
    }
} for (const m of text(value).matchAll(/[가-힣]{2,}\s*\d+(?:\s*[-·]\s*\d+)?\s*공구/g))
    strong.add('compound:' + norm(m[0])); return { strong, weak }; }
export function sharedIdentity(a, b) { const x = tokenForms(a), y = tokenForms(b); const strong = [...x.strong].filter(k => y.strong.has(k)).sort(); const allY = new Set([...y.strong, ...y.weak]); const weak = [...new Set([...x.strong, ...x.weak])].filter(k => allY.has(k) && !strong.includes(k)).sort(); return { strong, weak }; }
const signals = t => new Set(Object.entries(_SIGNAL_TERMS).filter(([, terms]) => terms.some(w => t.toLowerCase().includes(w))).map(([k]) => k));
const join = (r, fields) => fields.map(k => text(r[k])).join(' ');
function day(v) { const s = text(v).slice(0, 10); if (!/^\d{4}-\d{2}-\d{2}$/.test(s))
    return null; const n = Date.parse(s + 'T00:00:00Z'); return Number.isFinite(n) && new Date(n).toISOString().slice(0, 10) === s ? n : null; }
export function dateScore(b, s) { const bd = day(b.source_date || b.snapshot_date), sd = day(s.source_date); if (bd === null || sd === null)
    return { points: 0, lag_days: null }; const lag = (sd - bd) / 86400000; return { points: new Date(bd).getUTCFullYear() === new Date(sd).getUTCFullYear() ? (lag >= 0 ? 8 : 4) : 0, lag_days: lag }; }
export function moneyScore(b, s) { const amount = integer(b.budget_amount, { missing: null }) ?? integer(b.appropriation_amount); const shopping = integer(s.amount); if (amount <= 0n || shopping <= 0n)
    return 0; return shopping * 100n <= amount * 110n ? 7 : shopping * 100n <= amount * 150n ? 4 : 0; }
export function scorePair(b, s) { if (s.request_integrity_valid === false)
    return { match: null, reason: 'REQUEST_INTEGRITY_CONFLICT', unsupported: [] }; const org = organizationBasis(b, s); if (!org.basis)
    return { match: null, reason: 'NO_SUPPORTED_ORGANIZATION_MATCH', unsupported: org.unsupported }; const cats = (Array.isArray(s.primary_categories) && s.primary_categories.length ? s.primary_categories : [s.primary_category]).map(x => text(x).toUpperCase()).filter(x => ['LIGHTING', 'POLE'].includes(x)); const category = text(b.primary_category).toUpperCase(); if (!cats.length || !['LIGHTING', 'POLE'].includes(category))
    return { match: null, reason: 'CATEGORY_UNSUPPORTED', unsupported: [] }; const bt = join(b, ['project_name', 'field_name', 'section_name', 'account_name']), st = join(s, ['delivery_req_name', 'detail_item_name', 'item_name', 'model_name']); const identity=sharedIdentity(bt,st),clueTokens=org.identityClues?ADMIN_IDENTITY_TOKENS:new Set(),strong=identity.strong.filter(k=>!clueTokens.has(k)),weak=identity.weak.filter(k=>!clueTokens.has(k)),ss=signals(st),shared=[...signals(bt)].filter(x=>ss.has(x)).sort(); if (!strong.length && !shared.length)
    return { match: null, reason: 'NO_PROJECT_OR_SIGNAL', unsupported: [] }; const date = dateScore(b, s), mp = moneyScore(b, s); let score = (org.basis === 'EXACT_ORG_NAME' ? 48 : 42) + (cats.includes(category) ? 20 : 10) + (shared.length ? Math.min(20, 10 + 5 * (shared.length - 1)) : 0) + (strong.length ? Math.min(22, 12 + 5 * (strong.length - 1)) : 0) + (weak.length ? Math.min(8, 4 + 2 * (weak.length - 1)) : 0) + date.points + mp; score = Math.min(100, score); if (score < 65)
    return { match: null, reason: 'BELOW_THRESHOLD', unsupported: [] }; const capped = (!strong.length||org.capped) && score >= 80; if (capped)
    score = 79; return { match: { score, level: score >= 80 ? 'HIGH' : 'CANDIDATE', organization_basis: org.basis, shared_org_aliases: org.shared, shared_tokens: strong, weak_shared_tokens: weak, shared_signals: shared, lag_days: date.lag_days, project_identity_basis: strong.length ? 'DISTINCTIVE_SHARED_TOKEN' : weak.length ? 'WEAK_ADMIN_STEM' : 'GENERIC_LIGHTING_ONLY', evidence: [org.basis, ...(org.evidence||[]), cats.includes(category) ? 'CATEGORY_EXACT' : 'CATEGORY_RELATED', ...(shared.length ? ['SHARED_SIGNAL:' + shared.join(',')] : []), ...(strong.length ? ['DISTINCTIVE_SHARED_TOKEN:' + strong.slice(0, 5).join(',')] : []), ...(weak.length ? ['WEAK_ADMIN_STEM:' + weak.slice(0, 5).join(',')] : []), ...(date.points ? ['SAME_FISCAL_YEAR'] : []), ...(mp ? ['AMOUNT_PLAUSIBLE'] : []), ...(capped ? [org.capped?(org.basis==='ADMIN_PARENT_WARD_SCOPE_CANDIDATE'?'HIGH_CAPPED_PARENT_WARD_RELATION':'HIGH_CAPPED_AMBIGUOUS_ADMINISTRATIVE_SCOPE'):'HIGH_CAPPED_NO_DISTINCTIVE_PROJECT_TOKEN'] : [])] }, reason: null, unsupported: [] }; }
const projectKey = r => text(r.budget_project_identity) || text(r.budget_raw_source_key);
const assignmentKey = r => text(r.shopping_delivery_req_no) ? JSON.stringify(['REQUEST', text(r.shopping_delivery_req_no)]) : text(r.shopping_source_key) ? JSON.stringify(['SOURCE', text(r.shopping_source_key)]) : JSON.stringify(['FALLBACK', r.shopping_date ?? '', r.shopping_org ?? '', r.shopping_category ?? '', r.shopping_item ?? '', r.shopping_delivery_name ?? '', r.shopping_vendor ?? '', integer(r.shopping_amount).toString()]);
function rank(r) { const b = integer(r.budget_amount), s = integer(r.shopping_amount), gap = b > 0n && s > 0n ? (b > s ? b - s : s - b) : null; const lag = r.lag_days; return [-Number(r.score || 0), r.organization_basis === 'EXACT_ORG_NAME' ? -1 : 0, -(r.shared_signals?.length || 0), -(r.shared_tokens?.length || 0), gap === null ? 1 : 0, gap ?? 0n, lag !== null && lag !== undefined && lag >= 0 ? 0 : 1, lag === null || lag === undefined ? Infinity : Math.abs(lag), projectKey(r)]; }
function canonical(v) { if (Array.isArray(v))
    return '[' + v.map(canonical).join(',') + ']'; if (v && typeof v === 'object')
    return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}'; return JSON.stringify(typeof v === 'bigint' ? v.toString() : v) ?? 'null'; }
export function selectWinners(candidates) { const m = new Map(); for (const r of candidates) {
    if (!projectKey(r))
        throw Error('PROJECT_ID_REQUIRED');
    const key = assignmentKey(r), old = m.get(key);
    if (!old || compare(rank(r), rank(old)) < 0 || (compare(rank(r), rank(old)) === 0 && canonical(r) < canonical(old)))
        m.set(key, r);
} return [...m.entries()].sort(([a], [b]) => cmp(a, b)).map(([, r]) => r); }
