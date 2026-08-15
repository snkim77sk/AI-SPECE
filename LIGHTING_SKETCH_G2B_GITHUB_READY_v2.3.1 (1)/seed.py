import datetime as dt
import hashlib
import random
from db import connect, init_db

VENDORS = [
    '주식회사 라이팅스케치','인천엘이디','루멘테크','대한조명','세광라이팅',
    '한빛조명','에코라이트','미래엘이디','제이케이조명','스마트라이팅','광명테크','유니온조명'
]
ORGS = [
    ('인천도시공사','인천광역시'),('인천광역시 서구','인천광역시'),('인천광역시 남동구','인천광역시'),
    ('인천광역시 연수구','인천광역시'),('인천광역시 중구','인천광역시'),('인천광역시교육청','인천광역시'),
    ('서울주택도시공사','서울특별시'),('서울특별시 강서구','서울특별시'),('한국토지주택공사','경기도'),
    ('경기도 부천시','경기도'),('경기도 김포시','경기도'),('경기도 시흥시','경기도')
]
ITEMS = [
    ('LED가로등기구','3911160302','LED가로등기구','LS-ST-150','150W',310000),
    ('LED가로등기구','3911160302','LED가로등기구','LS-ST-200','200W',420000),
    ('LED보안등기구','3911160802','LED보안등기구','LS-SL-050','50W',165000),
    ('LED보안등기구','3911160802','LED보안등기구','LS-SL-075','75W',198000),
    ('LED실내조명등','3911210201','LED실내조명등','LS-PL-040','40W',72000),
    ('LED다운라이트','3911151502','LED다운라이트','LS-DL-020','20W',42000),
    ('LED투광등기구','3911161102','LED투광등기구','LS-FL-200','200W',285000),
    ('LED경관조명기구','3911160501','LED경관조명기구','LS-LS-030','30W',235000),
]


def _dates(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def seed_if_empty():
    init_db()
    with connect() as conn:
        n = conn.execute('SELECT COUNT(*) c FROM shopping_contracts').fetchone()['c']
        if n:
            return 0
    rng = random.Random(20260814)
    today = dt.date(2026, 8, 14)
    start = dt.date(2024, 1, 1)
    rows = []
    seq = 10000000
    day = start
    while day <= today:
        if rng.random() < 0.23:
            count = 1 + (1 if rng.random() < 0.28 else 0)
            for _ in range(count):
                org, region = rng.choice(ORGS)
                detail, cls, item_name, model, watt, base_price = rng.choice(ITEMS)
                vendor = rng.choices(VENDORS, weights=[12,10,9,8,8,7,7,6,6,5,4,4], k=1)[0]
                qty = rng.randint(3, 180)
                price = int(round(base_price * rng.uniform(0.91, 1.12) / 1000) * 1000)
                amount = price * qty
                seq += 1
                item_id = str(seq)
                contract = f'{org} {detail} 구매 및 교체공사'
                source_key = 'SAMPLE-' + hashlib.sha1(f'{day}|{org}|{vendor}|{item_id}'.encode()).hexdigest()
                rows.append((
                    day.isoformat(), org, region, org, contract, detail, item_id,
                    f'{item_name}, {vendor}, {model}, {watt}', model, price, qty, amount,
                    vendor, '', f'S-{day:%Y%m%d}-{seq}', f'D-{seq}', source_key, 1
                ))
        day += dt.timedelta(days=1)
    with connect() as conn:
        conn.executemany('''
            INSERT INTO shopping_contracts(
                base_date,demand_org,demand_region,top_org,contract_name,detail_item_name,item_id,item_name,model_name,
                unit_price,quantity,supply_amount,vendor_name,vendor_bizno,contract_no,delivery_req_no,source_key,is_sample
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', rows)

        # 입찰 샘플
        for i in range(120):
            d = today - dt.timedelta(days=rng.randint(0, 180))
            org, region = rng.choice(ORGS)
            detail, _, _, _, _, _ = rng.choice(ITEMS)
            budget = rng.randint(3000, 90000) * 10000
            notice_no = f'R26BK{70000000+i}'
            conn.execute('''
                INSERT OR IGNORE INTO bids(
                    notice_no,notice_order,notice_date,close_date,open_date,notice_name,notice_org,demand_org,region,
                    business_type,method_name,budget_amount,estimated_price,url,source_key,is_sample
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            ''', (
                notice_no,'000',d.isoformat(),(d+dt.timedelta(days=7)).isoformat(),(d+dt.timedelta(days=8)).isoformat(),
                f'{org} {detail} 구매 설치',org,org,region,'물품','일반경쟁',budget,int(budget*0.985),'',f'SAMPLE-BID-{notice_no}'
            ))
        # 예산 샘플
        for year in (2025, 2026, 2027):
            for i in range(28):
                org, region = rng.choice(ORGS)
                detail, _, _, _, _, _ = rng.choice(ITEMS)
                amount = rng.randint(5_000, 120_000) * 10000
                conn.execute('''
                    INSERT INTO budget_items(fiscal_year,region,org_name,project_name,category,budget_amount,status,source,is_sample)
                    VALUES (?,?,?,?,?,?,?,?,1)
                ''', (year,region,org,f'{org} {detail} 교체 및 개선사업',detail,amount,rng.choice(['본예산','추경','계획']), '샘플 데이터'))
    return len(rows)


def clear_samples():
    with connect() as conn:
        a = conn.execute('DELETE FROM shopping_contracts WHERE is_sample=1').rowcount
        b = conn.execute('DELETE FROM bids WHERE is_sample=1').rowcount
        c = conn.execute('DELETE FROM budget_items WHERE is_sample=1').rowcount
    return a+b+c

if __name__ == '__main__':
    print('seeded', seed_if_empty())
