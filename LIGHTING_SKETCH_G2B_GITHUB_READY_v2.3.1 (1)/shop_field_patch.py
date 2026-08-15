"""Runtime mapping fix for ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList.

The live G2B response uses field names that differ from older aliases handled by
v2.3.x.  Apply these aliases without changing the proven bid/service collectors.
"""
import hashlib


def apply_patch():
    import g2b_sync as g

    def normalize_shop_item(d):
        # Live response examples include dlvrReqRcptDate / fnlDlvrReqYn /
        # prdctIdntNoNm / prdctQty / prdctUprc / dlvrTmlmtDate /
        # cntrctCnclsStleNm.
        contract_date = g._date(g._pick(
            d, 'cntrctDt', 'contractDt', 'contractDate',
            'intllCntrctDlvrReqDate'
        ))
        delivery_req_date = g._date(g._pick(
            d, 'dlvrReqRcptDate', 'dlvrReqDt', 'deliveryReqDt', 'reqDt',
            'dlvrReqDate', 'intllCntrctDlvrReqDate'
        ))
        base_date = delivery_req_date or contract_date or g._date(g._pick(d, 'baseDt'))

        demand_org = g._pick(
            d, 'dminsttNm', 'demandInsttNm', 'demandOrgNm', 'orderInsttNm', 'insttNm'
        )
        direct_region = g._pick(
            d, 'dminsttRgnNm', 'demandRegionNm', 'rgnNm', 'regionName'
        )
        address = g._pick(
            d, 'dminsttAddr', 'demandInsttAddr', 'dlvrDstnAddr', 'addr'
        )
        vendor = g._pick(
            d, 'corpNm', 'cntrctCorpNm', 'entrpsNm', 'vendorNm', 'supplierNm',
            'cntrctCorpName'
        )

        detail_item_no = str(g._pick(
            d, 'dtilPrdctClsfcNo', 'detailPrdctClsfcNo', 'detailItemNo',
            'prdctClsfcNo'
        ))
        detail_item = g._pick(
            d, 'dtilPrdctClsfcNoNm', 'dtilPrdctClsfcNm', 'detailPrdctNm',
            'detailItemName', 'prdctClsfcNoNm', 'prdctClsfcNm'
        )
        item_id = str(g._pick(
            d, 'prdctIdntNo', 'goodsIdntNo', 'itemId', 'identificationNo'
        ))
        item_name = g._pick(
            d, 'prdctIdntNoNm', 'prdctIdntNm', 'goodsIdntNm', 'itemName', 'prdctNm'
        )
        model_name = g._pick(
            d, 'modelNm', 'modelName', 'goodsModelNm', 'prdctSpecNm', 'specNm'
        )
        unit = g._pick(d, 'prdctUnit', 'unit', 'unitNm', 'dlvrUnit')
        unit_price = int(round(g._num(g._pick(
            d, 'prdctUprc', 'unitPric', 'unitPrice', 'cntrctUnitPric',
            'cntrctPrce', 'prc'
        ))))
        qty = g._num(g._pick(
            d, 'prdctQty', 'dlvrReqQty', 'reqQty', 'quantity', 'qty'
        ))
        amount = int(round(g._num(g._pick(
            d, 'dlvrReqAmt', 'reqAmt', 'supplyAmount', 'amount', 'dlvrAmt'
        ))))
        if not amount and unit_price and qty:
            amount = int(round(unit_price * qty))

        contract_name = g._pick(
            d, 'dlvrReqNm', 'cntrctNm', 'contractNm', 'deliveryReqNm',
            'bizNm', 'dlvrReqSj'
        )
        contract_no = str(g._pick(d, 'cntrctNo', 'contractNo'))
        delivery_req_no = str(g._pick(d, 'dlvrReqNo', 'deliveryReqNo', 'reqNo'))
        detail_seq = str(g._pick(
            d, 'dlvrReqChgOrd', 'dlvrReqDtlSeq', 'dlvrReqDtlSn', 'dlvrReqSeq',
            'detailSeq', 'seq'
        ))
        bizno = str(g._pick(
            d, 'cntrctCorpBizno', 'corpBizno', 'bizno', 'bizrno'
        ))
        final_yn = str(g._pick(
            d, 'fnlDlvrReqYn', 'lastDlvrReqYn', 'finalDlvrReqYn', 'finalYn',
            'lastYn', 'fnlYn'
        ))
        contract_method = g._pick(
            d, 'cntrctCnclsStleNm', 'cntrctMthdNm', 'contractMthdNm',
            'contractMethodNm', 'cntrctMthd'
        )
        delivery_deadline = g._date(g._pick(
            d, 'dlvrTmlmtDate', 'dlvrTmlmtDt', 'deliveryDeadline',
            'dlvrDueDt', 'deliveryDueDate'
        ))

        if delivery_req_no:
            rawkey = '|'.join([
                'DLVR', delivery_req_no, detail_seq, item_id, contract_no, vendor
            ])
        else:
            rawkey = '|'.join([
                'FALLBACK', base_date, demand_org, vendor, item_id,
                contract_no, contract_name
            ])

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

    g.normalize_shop_item = normalize_shop_item
    return True
