"""Independent HTTP semantics for the three complete showcase applications."""
import csv,http.client,io,json,re,time
from pathlib import Path
from tests.http_smoke import running
ROOT=Path(__file__).resolve().parents[1]

def get(port,path,method='GET'):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=20)
    try:
        connection.request(method,path);response=connection.getresponse();body=response.read();headers={key.lower():value for key,value in response.getheaders()}
        assert response.status<500,(path,response.status,body[:200])
        assert re.fullmatch(r'render;dur=\d+\.\d{3}',headers.get('server-timing','')),(path,headers)
        if method!='HEAD':assert int(headers['content-length'])==len(body)
        return response.status,headers,body
    finally:connection.close()

def main():
    with running(ROOT/'build/showcase/ssr-site/server') as port:
        first=get(port,'/');time.sleep(.005);second=get(port,'/')
        assert first[0]==200 and b'Good spaces.' in first[2] and b'Courtyard House' in first[2]
        stamp=lambda wire:int(re.search(rb'<code>(\d+)</code>',wire)[1])
        assert stamp(second[2])>stamp(first[2]);assert 'no-store' in first[1]['cache-control']
        assert b'A small studio.' in get(port,'/studio')[2]
        assert get(port,'/courtyard.svg')[1]['content-type'].startswith('image/svg')
        assert get(port,'/','HEAD')[2]==b''
    with running(ROOT/'build/showcase/blog/server') as port:
        assert get(port,'/')[2].count(b'class="story-card"')==3
        filtered=get(port,'/?category=engineering')[2];assert filtered.count(b'class="story-card"')==1 and b'Make room for a smaller web' in filtered
        assert get(port,'/?q=mornings')[2].count(b'class="story-card"')==1
        hostile=get(port,'/?q=%22%3E%3Cscript%3Ebad%3C/script%3E')[2];assert b'<script>bad' not in hostile and b'&lt;script&gt;' in hostile
        assert b'No stories found' in get(port,'/?q=not-a-real-story')[2]
        for slug in ['a-smaller-web','slower-mornings','quiet-interfaces']:
            status,_,body=get(port,'/journal/'+slug);assert status==200 and body.count(b'<section><h2>')==4
        assert get(port,'/journal/missing')[0]==404
    with running(ROOT/'build/showcase/saas/server') as port:
        for days in (7,30,90):
            status,_,body=get(port,f'/?range={days}');assert status==200 and body.count(b'data-chart-value=')==days
            api=json.loads(get(port,f'/api/metrics?range={days}')[2]);expected=[1400+i*31+(i*79+days*17)%600 for i in range(days)]
            assert api['demo'] and api['revenue']==expected and api['period_days']==days and api['records']==1000
            assert f'${sum(expected):,}'.encode() in body
        large=get(port,'/customers?records=10000')[2];assert large.count(b'data-customer-id=')==25 and b'10,000' in large
        one=re.findall(rb'data-customer-id="(\d+)"',large);two=re.findall(rb'data-customer-id="(\d+)"',get(port,'/customers?records=10000&page=2')[2]);assert set(one).isdisjoint(two)
        last=get(port,'/customers?records=10000&page=400')[2];assert b'data-customer-id="10000"' in last and last.count(b'data-customer-id=')==25
        filtered=get(port,'/customers?q=Orbit&plan=Pro')[2];ids=list(map(int,re.findall(rb'data-customer-id="(\d+)"',filtered)));assert ids and all((i-1)%12==1 and 5<=(i-1)%9<8 for i in ids)
        assert b'No matching customers' in get(port,'/customers?q=unmatched-workspace')[2]
        _,headers,data=get(port,'/api/export-customers?q=Orbit&plan=Pro');rows=list(csv.DictReader(io.StringIO(data.decode())));assert rows and all('Orbit' in row['company'] and row['plan']=='Pro' for row in rows);assert 'attachment;' in headers['content-disposition']
        assert get(port,'/activity')[2].count(b'class="event"')==18
    print('PASS showcases: fresh SSR/timing, blog search/articles/escaping, computed charts/API, 10k pagination/filtering, CSV and activity',flush=True)
if __name__=='__main__':main()
