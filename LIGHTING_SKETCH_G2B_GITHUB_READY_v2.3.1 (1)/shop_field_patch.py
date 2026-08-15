"""Runtime compatibility patch for G2B shopping contract collection and UI.

The live ShoppingMallPrdctInfoService response uses field names that differ from
older aliases. This module also keeps the shopping-screen filters aligned with
the exact detail-item-number collection policy.
"""
import hashlib

TARGET_DETAIL_ITEMS = {
    'LED다운라이트': '3911151502',
    'LED가로등기구': '3911160302',
    'LED터널용등기구': '3911160304',
    'LED경관조명기구': '3911160501',
    'LED보안등기구': '3911160802',
    'LED투광등기구': '3911161102',
    'LED실내조명등': '3911210201',
    '철제가로등주': '3911152601',
    '스테인리스가로등주': '3911152602',
    '가로등주부속자재': '3911152607',
    '태양광발전장치': '2611160701',
    '분전반': '3912110101',
}

FALLBACK_TERMS = (
    'LED다운라이트', 'LED가로등', 'LED터널', 'LED경관', 'LED보안등', 'LED투광등',
    'LED실내조명', '철제가로등주', '스테인리스가로등주', '가로등주부속자재',
    '태양광발전장치', '분전반', '분전함',
)


def _digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def apply_patch():
    import g2b_sync as g

    target_codes = set(TARGET_DETAIL_ITEMS.values())

    def normalize_shop_item(d):
        contract_date = g._date(g._pick(
            d, 'cntrctDt', 'contractDt', 'contractDate', 'intllCntrctDlvrReqDate'
        ))
        delivery_req_date = g._date(g._pick(
            d, 'dlvrReqRcptDate', 'dlvrReqDt', 'deliveryReqDt', 'reqDt',
            'dlvrReqDate', 'intllCntrctDlvrReqDate'
        ))
        base_date = delivery_req_date or contract_date or g._date(g._pick(d, 'baseDt'))

        demand_org = g._pick(d, 'dminsttNm', 'demandInsttNm', 'demandOrgNm', 'orderInsttNm', 'insttNm')
        direct_region = g._pick(d, 'dminsttRgnNm', 'demandRegionNm', 'rgnNm', 'regionName')
        address = g._pick(d, 'dminsttAddr', 'demandInsttAddr', 'dlvrDstnAddr', 'addr')
        vendor = g._pick(d, 'corpNm', 'cntrctCorpNm', 'entrpsNm', 'vendorNm', 'supplierNm', 'cntrctCorpName')

        detail_item_no = _digits(g._pick(
            d, 'dtilPrdctClsfcNo', 'detailPrdctClsfcNo', 'detailItemNo', 'prdctClsfcNo'
        ))
        detail_item = g._pick(
            d, 'dtilPrdctClsfcNoNm', 'dtilPrdctClsfcNm', 'detailPrdctNm',
            'detailItemName', 'prdctClsfcNoNm', 'prdctClsfcNm'
        )
        item_id = _digits(g._pick(d, 'prdctIdntNo', 'goodsIdntNo', 'itemId', 'identificationNo'))
        item_name = g._pick(d, 'prdctIdntNoNm', 'prdctIdntNm', 'goodsIdntNm', 'itemName', 'prdctNm')
        model_name = g._pick(d, 'modelNm', 'modelName', 'goodsModelNm', 'prdctSpecNm', 'specNm')
        unit = g._pick(d, 'prdctUnit', 'unit', 'unitNm', 'dlvrUnit')
        unit_price = int(round(g._num(g._pick(
            d, 'prdctUprc', 'unitPric', 'unitPrice', 'cntrctUnitPric', 'cntrctPrce', 'prc'
        ))))
        qty = g._num(g._pick(d, 'prdctQty', 'dlvrReqQty', 'reqQty', 'quantity', 'qty'))
        amount = int(round(g._num(g._pick(d, 'dlvrReqAmt', 'reqAmt', 'supplyAmount', 'amount', 'dlvrAmt'))))
        if not amount and unit_price and qty:
            amount = int(round(unit_price * qty))

        contract_name = g._pick(d, 'dlvrReqNm', 'cntrctNm', 'contractNm', 'deliveryReqNm', 'bizNm', 'dlvrReqSj')
        contract_no = str(g._pick(d, 'cntrctNo', 'contractNo'))
        delivery_req_no = str(g._pick(d, 'dlvrReqNo', 'deliveryReqNo', 'reqNo'))
        detail_seq = str(g._pick(d, 'dlvrReqChgOrd', 'dlvrReqDtlSeq', 'dlvrReqDtlSn', 'dlvrReqSeq', 'detailSeq', 'seq'))
        bizno = str(g._pick(d, 'cntrctCorpBizno', 'corpBizno', 'bizno', 'bizrno'))
        final_yn = str(g._pick(d, 'fnlDlvrReqYn', 'lastDlvrReqYn', 'finalDlvrReqYn', 'finalYn', 'lastYn', 'fnlYn'))
        contract_method = g._pick(d, 'cntrctCnclsStleNm', 'cntrctMthdNm', 'contractMthdNm', 'contractMethodNm', 'cntrctMthd')
        delivery_deadline = g._date(g._pick(d, 'dlvrTmlmtDate', 'dlvrTmlmtDt', 'deliveryDeadline', 'dlvrDueDt', 'deliveryDueDate'))

        if delivery_req_no:
            rawkey = '|'.join(['DLVR', delivery_req_no, detail_seq, item_id, contract_no, vendor])
        else:
            rawkey = '|'.join(['FALLBACK', base_date, demand_org, vendor, item_id, contract_no, contract_name])

        return {
            'base_date': base_date,
            'contract_date': contract_date,
            'delivery_req_date': delivery_req_date,
            'final_yn': final_yn,
            'demand_org': demand_org,
            'demand_region': g.infer_region(demand_org, address, direct_region),
            'top_org': g.normalize_top_org(demand_org),
            'contract_name': contract_name,
            'contract_method': contract_method,
            'delivery_deadline': delivery_deadline,
            'detail_item_no': detail_item_no,
            'detail_item_name': detail_item,
            'item_id': item_id,
            'item_name': item_name,
            'model_name': model_name,
            'unit': unit,
            'unit_price': unit_price,
            'quantity': qty,
            'supply_amount': amount,
            'vendor_name': vendor,
            'vendor_bizno': bizno,
            'contract_no': contract_no,
            'delivery_req_no': delivery_req_no,
            'delivery_req_detail_seq': detail_seq,
            'source_key': hashlib.sha1(rawkey.encode('utf-8')).hexdigest(),
        }

    def upsert_shop(items, target_only=True):
        count = 0
        matched = 0
        skipped = 0
        with g.connect() as conn:
            for raw in items:
                x = normalize_shop_item(raw)
                if not x['base_date'] or not x['item_id']:
                    skipped += 1
                    continue

                code = _digits(x['detail_item_no'])
                if target_only:
                    if code:
                        if code not in target_codes:
                            continue
                    else:
                        hay = ' '.join([
                            x['detail_item_name'], x['item_name'], x['contract_name'], x['vendor_name']
                        ])
                        if not g._matches(hay, FALLBACK_TERMS):
                            continue
                matched += 1

                conn.execute('''
                    INSERT INTO shopping_contracts(
                        base_date,contract_date,delivery_req_date,final_yn,demand_org,demand_region,top_org,
                        contract_name,contract_method,delivery_deadline,detail_item_no,detail_item_name,item_id,item_name,model_name,unit,
                        unit_price,quantity,supply_amount,vendor_name,vendor_bizno,contract_no,delivery_req_no,delivery_req_detail_seq,source_key,is_sample
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                    ON CONFLICT DO UPDATE SET
                        base_date=excluded.base_date,contract_date=excluded.contract_date,delivery_req_date=excluded.delivery_req_date,
                        final_yn=excluded.final_yn,demand_org=excluded.demand_org,demand_region=excluded.demand_region,
                        top_org=excluded.top_org,contract_name=excluded.contract_name,detail_item_name=excluded.detail_item_name,
                        contract_method=excluded.contract_method,delivery_deadline=excluded.delivery_deadline,
                        detail_item_no=excluded.detail_item_no,item_id=excluded.item_id,item_name=excluded.item_name,
                        model_name=excluded.model_name,unit=excluded.unit,unit_price=excluded.unit_price,
                        quantity=excluded.quantity,supply_amount=excluded.supply_amount,vendor_name=excluded.vendor_name,
                        vendor_bizno=excluded.vendor_bizno,contract_no=excluded.contract_no,delivery_req_no=excluded.delivery_req_no,
                        delivery_req_detail_seq=excluded.delivery_req_detail_seq,is_sample=0,
                        updated_at=datetime('now','localtime')
                ''', tuple(x[k] for k in [
                    'base_date','contract_date','delivery_req_date','final_yn','demand_org','demand_region','top_org',
                    'contract_name','contract_method','delivery_deadline','detail_item_no','detail_item_name','item_id','item_name','model_name','unit',
                    'unit_price','quantity','supply_amount','vendor_name','vendor_bizno','contract_no','delivery_req_no','delivery_req_detail_seq','source_key'
                ]))
                count += 1
        return count, matched, skipped

    g.normalize_shop_item = normalize_shop_item
    g.upsert_shop = upsert_shop

    # server.py is safe to import here; importing it only defines the HTTP server.
    # It is started later by app.py. Patch its UI/filter globals before startup.
    import server

    server.DETAIL_ITEMS = list(TARGET_DETAIL_ITEMS.keys())
    original_where_shop = server.where_shop

    def where_shop(start, end, region='', q='', items=None, vendor=''):
        # Build the common clauses without the legacy detail_item_name filter.
        where, vals = original_where_shop(start, end, region, q, None, vendor)
        if items:
            codes = []
            fallback_names = []
            for item in items:
                text = str(item or '').strip()
                code = TARGET_DETAIL_ITEMS.get(text)
                if code:
                    codes.append(code)
                elif _digits(text) in target_codes:
                    codes.append(_digits(text))
                elif text:
                    fallback_names.append(text)
            sub = []
            if codes:
                sub.append('detail_item_no IN (%s)' % ','.join('?' for _ in codes))
                vals += codes
            if fallback_names:
                sub.append('detail_item_name IN (%s)' % ','.join('?' for _ in fallback_names))
                vals += fallback_names
            if sub:
                where += ' AND (' + ' OR '.join(sub) + ')'
        return where, vals

    server.where_shop = where_shop
    server.APP_VERSION = '2.3.9-ui-detail-code-filter'

    original_settings_html = server.settings_html

    def settings_html(msg='', error=False):
        text = original_settings_html(msg, error)
        text = text.replace('조명 대상 ', '세부품명번호 대상 ')
        return text

    server.settings_html = settings_html
    return True
