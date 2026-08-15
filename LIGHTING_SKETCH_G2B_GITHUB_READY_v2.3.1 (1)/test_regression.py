import http.client
import os
import tempfile
import threading
import unittest
import urllib.parse

_tmp = tempfile.TemporaryDirectory()
os.environ['G2B_DB_PATH'] = os.path.join(_tmp.name, 'test.sqlite3')
os.environ['G2B_OPEN_BROWSER'] = '0'

import db
import g2b_sync
import server


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        with db.connect() as conn:
            conn.execute('DELETE FROM shopping_contracts')
            conn.execute('DELETE FROM bids')
            conn.execute('DELETE FROM users')

    def test_detail_number_groups_are_exact_and_disjoint(self):
        expected = {
            'led': {'3910161601','3911160301','3911160501','3911160801','3911161101','3911210201','3911210301'},
            'solar': {'2611160701','3912110101'},
            'pole': {'3911152601','3911152602','3911152607'},
        }
        self.assertEqual({k:set(v[1]) for k,v in server.GROUPS.items()}, expected)
        self.assertEqual(set.union(*expected.values()), set(g2b_sync.SHOP_DETAIL_ITEM_NOS))

    def test_amount_prefers_unit_price_times_quantity_and_upsert_repairs(self):
        raw={'dlvrReqDt':'20260815','dminsttNm':'기관','dtilPrdctClsfcNo':'3911160301','dtilPrdctClsfcNm':'LED가로등기구','prdctIdntNo':'ITEM1','unitPric':'1200','dlvrReqQty':'3','dlvrReqAmt':'999999','dlvrReqNo':'REQ1','dlvrReqDtlSeq':'1'}
        self.assertEqual(g2b_sync.normalize_shop_item(raw)['supply_amount'],3600)
        g2b_sync.upsert_shop([raw])
        raw['unitPric']='1500'
        g2b_sync.upsert_shop([raw])
        with db.connect() as conn:
            rows=conn.execute('SELECT unit_price,quantity,supply_amount FROM shopping_contracts').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['unit_price'],rows[0]['quantity'],rows[0]['supply_amount']),(1500,3,4500))

    def test_first_admin_login_group_routes_and_scoped_reset(self):
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
        conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
        try:
            conn.request('GET','/dashboard'); r=conn.getresponse(); r.read(); self.assertEqual(r.getheader('Location'),'/setup-admin')
            body=urllib.parse.urlencode({'username':'owner','password':'VerySafe123!','password_confirm':'VerySafe123!'})
            conn.request('POST','/setup-admin',body,{'Content-Type':'application/x-www-form-urlencoded'}); r=conn.getresponse(); r.read(); self.assertEqual(r.getheader('Location'),'/login')
            body=urllib.parse.urlencode({'username':'owner','password':'VerySafe123!'})
            conn.request('POST','/login',body,{'Content-Type':'application/x-www-form-urlencoded'}); r=conn.getresponse(); r.read(); cookie=r.getheader('Set-Cookie').split(';',1)[0]
            self.assertEqual(r.getheader('Location'),'/dashboard')
            for group,numbers in (('led',server.GROUPS['led'][1]),('solar',server.GROUPS['solar'][1]),('pole',server.GROUPS['pole'][1])):
                path=f'/g2b/shopping/prdct_detail.php?group={group}&page=2&view=detail'
                conn.request('GET',path,headers={'Cookie':cookie}); r=conn.getresponse(); page=r.read().decode()
                self.assertEqual(r.status,200); self.assertIn(f'name="group" value="{group}"',page); self.assertIn(f'group={group}',page)
                for number in numbers: self.assertIn(number,page)
            with db.connect() as dbc:
                dbc.execute("INSERT INTO shopping_contracts(base_date,item_id,detail_item_no,source_key,is_sample) VALUES ('2026-08-15','R','3911160301','real',0)")
                dbc.execute("INSERT INTO bids(notice_no,source_key,business_type) VALUES ('B','bid','용역')")
            body=urllib.parse.urlencode({'_csrf':server.csrf_token('/reset-shopping-data'),'confirmation':'쇼핑몰 실데이터 초기화'})
            conn.request('POST','/reset-shopping-data',body,{'Content-Type':'application/x-www-form-urlencoded','Cookie':cookie}); r=conn.getresponse(); r.read()
            with db.connect() as dbc:
                self.assertEqual(dbc.execute('SELECT COUNT(*) FROM shopping_contracts').fetchone()[0],0)
                self.assertEqual(dbc.execute('SELECT COUNT(*) FROM bids').fetchone()[0],1)
                user=dbc.execute('SELECT username,password_hash,role,status FROM users').fetchone()
            self.assertEqual((user['username'],user['role'],user['status']),('owner','admin','active'))
            self.assertNotIn('VerySafe123!',user['password_hash'])
        finally:
            conn.close(); httpd.shutdown(); httpd.server_close()


if __name__ == '__main__':
    unittest.main()
