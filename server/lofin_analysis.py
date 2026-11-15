"""Read-only local budget analysis. No credentials, network, or source mutations."""
import argparse
import hashlib
import heapq
import html
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'lofin_candidate'))
from budget_projection_vnext import project_payload
from receipt_validation import verify_receipt


def amount(value):
    if value is None or isinstance(value,bool) or not str(value).strip(): return None
    try:
        parsed=Decimal(str(value).replace(',','').strip())
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def analyze(path):
    report={'generated_at_kst':datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds'),
        'read_only':True,'api_calls':0,'source_db':str(path),'rows':0,'layers':[],
        'combined_budget_total':None,'unit_verified':False,
        'coverage_note':'수집된 자료만 분석합니다. 다른 출처·기준일의 금액을 합산하지 않습니다.'}
    if not path.exists():
        report['state']='AWAITING_FIRST_VERIFIED_SOURCE_DATA';return report
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        for dataset in ('budget','budget_appropriation'):
            stamp=conn.execute('SELECT MAX(source_date) FROM raw_records WHERE dataset=?',(dataset,)).fetchone()[0]
            if not stamp: continue
            checkpoints=[dict(r) for r in conn.execute('''SELECT * FROM collection_checkpoints
                WHERE dataset=? AND (range_end=? OR dataset='budget_appropriation')''',(dataset,stamp))]
            if not checkpoints or any(not verify_receipt(conn,cp,complete=cp['status']=='COMPLETE') for cp in checkpoints):
                raise ValueError('ANALYSIS_REQUIRES_VERIFIED_RECEIPTS')
            if conn.execute('SELECT COUNT(*) FROM raw_records WHERE dataset=? AND source_date=?',(dataset,stamp)).fetchone()[0]!=sum(cp['saved_count'] for cp in checkpoints):
                raise ValueError('ANALYSIS_RAW_SCOPE_COUNT_MISMATCH')
            scopes=[{k:cp[k] for k in ('scope_key','page_no','status','saved_count','source_total')} for cp in checkpoints]
            totals={};present={};missing=0;rows=0;groups={};top=[];hash_errors=0;balance_sum=Decimal(0);paired_rows=0
            fields=('bdg_cash_amt','cpl_amt','ep_amt') if dataset=='budget' else (
                'biz_bdg_tott_amt','biz_bdg_prsm_amt','padm_oper_exps_tott_amt','padm_oper_prsm_exps','fin_acv_tott_amt','fin_acv_prsm_amt')
            for raw in conn.execute('SELECT source_key,source_date,payload_json,payload_sha256 FROM raw_records WHERE dataset=? AND source_date=?',(dataset,stamp)):
                if hashlib.sha256(raw['payload_json'].encode('utf-8')).hexdigest()!=raw['payload_sha256']:
                    hash_errors+=1;continue
                payload=json.loads(raw['payload_json'])
                fact=project_payload(dataset,payload,source_date=raw['source_date'])
                rows+=1
                values={key:amount(payload.get(key)) for key in fields}
                for key,value in values.items():
                    if value is not None:
                        totals[key]=totals.get(key,Decimal(0))+value;present[key]=present.get(key,0)+1
                budget=values[fields[0]]
                executed=values.get('ep_amt')
                remaining=budget-executed if budget is not None and executed is not None else None
                if remaining is not None: balance_sum+=remaining;paired_rows+=1
                if budget is None: missing+=1
                group=(fact['region_name'],fact['org_name'])
                if group not in groups and len(groups)>=10000: raise ValueError('ORGANIZATION_LIMIT_REQUIRES_REVIEW')
                g=groups.setdefault(group,{'region':group[0],'organization':group[1],'rows':0,'budget':Decimal(0),'budget_rows':0})
                g['rows']+=1
                if budget is not None: g['budget']+=budget;g['budget_rows']+=1
                row={'region':fact['region_name'],'organization':fact['org_name'],'department':fact['dept_name'],
                    'project':fact['project_name'],'account':fact['account_name'],'budget':str(budget) if budget is not None else None,
                    'executed':str(executed) if executed is not None else None,'remaining':str(remaining) if remaining is not None else None,
                    'execution_rate_percent':str((executed/budget*100).quantize(Decimal('0.01'))) if budget is not None and budget>0 and executed is not None else None,
                    'administration_budget':str(values['padm_oper_exps_tott_amt']) if values.get('padm_oper_exps_tott_amt') is not None else None,
                    'financial_activity_budget':str(values['fin_acv_tott_amt']) if values.get('fin_acv_tott_amt') is not None else None,
                    'source_key':raw['source_key'],'raw_amounts':{key:value for key,value in payload.items() if 'amt' in key.lower() or key=='padm_oper_prsm_exps'}}
                rank=budget if budget is not None else Decimal('-Infinity')
                entry=(rank,raw['source_key'],row)
                if len(top)<25: heapq.heappush(top,entry)
                elif entry[:2]>top[0][:2]: heapq.heapreplace(top,entry)
            # AIDFA contains structural layers: never present their sum as a full budget.
            sums={k:str(v) for k,v in totals.items()} if dataset=='budget' else None
            layer={'dataset':dataset,'source':'QWGJK' if dataset=='budget' else 'AIDFA','snapshot':stamp,
                   'rows':rows,'scope_status':scopes,'raw_hash_errors':hash_errors,'missing_budget_rows':missing,
                   'amount_field_nonempty_counts':present,'collected_business_amount_sums':sums,
                   'remaining_for_rows_with_both_values':str(balance_sum) if paired_rows else None,
                   'remaining_paired_rows':paired_rows,
                   'aggregation_status':'COLLECTED_BUSINESSES_ONLY_NOT_CONSOLIDATED' if dataset=='budget' else 'STRUCTURAL_ROWS_NOT_ADDED',
                   'organizations':[dict(g,budget=str(g['budget']) if dataset=='budget' else None) for g in sorted(groups.values(),key=lambda g:g['budget'],reverse=True)],
                   'top_rows':[entry[2] for entry in sorted(top,key=lambda e:e[:2],reverse=True)]}
            report['layers'].append(layer);report['rows']+=rows
    report['state']='LOCAL_DATA_AVAILABLE' if report['rows'] else 'NO_NONEMPTY_SOURCE_DATA'
    if any(layer['raw_hash_errors'] for layer in report['layers']):
        report['state']='HOLD_RAW_INTEGRITY_ERROR'
    return report


def render(report,g2b=None,lofin=None):
    esc=lambda value:html.escape(str(value if value is not None else '미확인'))
    cards=[]
    for label,status in [('나라장터',g2b or {}),('지방재정365',lofin or {})]:
        cards.append(f'<article><h2>{label}</h2><strong>{esc(status.get("state","대기"))}</strong><p>이번 회차 호출 {esc(status.get("attempted",status.get("actual_attempts",0)))}회 · 검증 저장 {esc(status.get("verified_rows",0))}건</p></article>')
    sections=[]
    for layer in report['layers']:
        heading='세부사업 예산·집행' if layer['source']=='QWGJK' else '구조별 세출예산'
        body=[]
        keys=('organization','project','account','budget','executed','remaining','execution_rate_percent') if layer['source']=='QWGJK' else ('organization','project','account','budget','administration_budget','financial_activity_budget')
        headers=('기관','사업·분야','회계','예산현액','집행','잔액','집행률 %') if layer['source']=='QWGJK' else ('기관','분야·부문','회계','정책사업예산 총액','행정운영경비 총액','재무활동 총액')
        for row in layer['top_rows']:
            body.append('<tr>'+''.join('<td>'+esc(row.get(k))+'</td>' for k in keys)+'</tr>')
        coverage=' / '.join(f'{s["saved_count"]:,} / {s["source_total"]:,}건 · {s["status"]}' for s in layer['scope_status'])
        sections.append(f'<section><h2>{heading} <small>{layer["source"]}</small></h2><p>기준일 {esc(layer["snapshot"])} · {esc(coverage)}</p><p>수집 자료 중 금액 상위 최대 25건. 미확인은 0원으로 처리하지 않습니다.</p><div class="table"><table><thead><tr>{"".join("<th>"+h+"</th>" for h in headers)}</tr></thead><tbody>{"".join(body)}</tbody></table></div></section>')
    if not sections: sections=['<section><h2>검증된 예산 자료를 기다리고 있습니다</h2><p>첫 API 응답의 저장·재조회가 끝나면 실제 자료로 분석합니다. 시험 자료는 표시하지 않습니다.</p></section>']
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>G2B 예산 수집·분석</title>
    <style>body{{font-family:system-ui,"Malgun Gothic",sans-serif;background:#f2f5fa;color:#18263d;margin:0}}main{{max-width:1250px;margin:auto;padding:36px 24px}}h1{{font-size:30px}}h2{{font-size:20px}}p{{line-height:1.6;color:#475569}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}}article,section{{background:white;padding:24px;border-radius:12px;margin-bottom:20px;box-shadow:0 2px 10px #1a32500a}}strong{{color:#175ca9}}small{{font-size:13px;color:#64748b}}.note{{border-left:4px solid #c58615;padding:12px 18px;background:#fff8e6}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #e2e8f0;white-space:nowrap}}th{{background:#edf3fa}}footer{{color:#64748b;font-size:13px}}</style>
    <main><small>서버 저장 자료 · 읽기 전용</small><h1>G2B 예산 수집·분석</h1><p>최신 기준일의 지방재정 자료를 기관·사업별로 확인합니다.</p><div class="cards">{''.join(cards)}</div>
    <p class="note">{esc(report['coverage_note'])} 금액은 API 원문 값이며 단위 확인 전에는 원화 총예산으로 확정하지 않습니다. 구조별 예산과 세부사업 예산은 합산하지 않습니다.</p>
    {''.join(sections)}<footer>갱신 {esc(report['generated_at_kst'])} · 분석용 API 호출 0회 · 로컬 프로그램에서 새로 생성하면 최신 저장 자료가 반영됩니다.</footer></main></html>'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state-root',type=Path,default=Path('D:/G2B/lofin-collection'))
    parser.add_argument('--output',type=Path,default=Path('D:/G2B/lofin-collection/budget-analysis.html'))
    args=parser.parse_args()
    report=analyze((args.state_root/'budget-2026.sqlite3').resolve())
    def status(path):
        try: return json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError): return None
    args.output.parent.mkdir(exist_ok=True)
    args.output.write_text(render(report,status(Path('D:/G2B/live-bounded-20261114/automatic-status.json')),
                                  status(args.state_root/'lofin-status.json')),encoding='utf-8')
    args.output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'state':report['state'],'rows_analyzed':report['rows'],'output':str(args.output),'api_calls':0}))


if __name__=='__main__': main()
