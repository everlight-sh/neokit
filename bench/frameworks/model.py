"""Independent Python oracle and HTML reader; excludes framework boot scripts."""
from html.parser import HTMLParser
import http.client
import time
from urllib.parse import urlencode

WORDS=('Widget','Gadget','Gizmo','Doohickey','Sprocket','Flange','Bracket','Grommet')
ADJECTIVES=('Deluxe','Basic','Pro','Mini','Ultra','Classic','Eco','Max')
SPECIAL='Ben & Jerry\'s "Special" <limited> café'
SCENARIOS=[{'name':'small-ssr','path':'/','n':3,'interactive':False},
           {'name':'large-ssr','path':'/large','n':1000,'interactive':False},
           {'name':'interactive-ssr','path':'/interactive','n':50,'interactive':True}]


def rows(n,seed):
    return [[str(i),WORDS[(i+seed)%8]+' '+ADJECTIVES[(i*7+seed)%8],str((i*37+seed*13)%1000)+'.99',str((i*13+seed)%50),SPECIAL if i%5==0 else '-'] for i in range(n)]


class Catalog(HTMLParser):
    def __init__(self,body):
        super().__init__(convert_charrefs=True)
        self.attrs={};self.rows=[];self.row=None;self.cell=None;self.in_rows=False;self.capture=None
        self.values={'request-token':'','render-stamp':'','checksum':''};self.scripts=[];self.styles=[];self.inline_scripts=[];self.script=None;self.counters=0
        self.feed(body.decode('utf-8'))

    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes)
        if tag=='main' and attrs.get('id')=='benchmark':self.attrs=attrs
        if tag=='tbody' and attrs.get('id')=='catalog-rows':self.in_rows=True
        if self.in_rows and tag=='tr':self.row=[]
        if self.in_rows and tag=='td':self.cell=[]
        if attrs.get('id') in self.values:self.capture=(tag,attrs['id'])
        if attrs.get('data-web-widget')=='counter':self.counters+=1
        if tag=='script':
            self.script=[]
            if attrs.get('src'):self.scripts.append(attrs['src'])
        if tag=='link' and attrs.get('href'):
            if attrs.get('rel')=='stylesheet':self.styles.append(attrs['href'])
            if attrs.get('rel')=='modulepreload' or attrs.get('rel')=='preload' and attrs.get('as')=='script':self.scripts.append(attrs['href'])

    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)

    def handle_data(self,text):
        if self.script is not None:self.script.append(text);return
        if self.cell is not None:self.cell.append(text)
        if self.capture:self.values[self.capture[1]]+=text

    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:
            self.inline_scripts.append(''.join(self.script).encode());self.script=None
        if tag=='td' and self.cell is not None:
            self.row.append(''.join(self.cell));self.cell=None
        if tag=='tr' and self.in_rows and self.row is not None:self.rows.append(self.row);self.row=None
        if tag=='tbody':self.in_rows=False
        if self.capture and tag==self.capture[0]:self.capture=None


def validate(body,n,seed,token,interactive,static=False):
    page=Catalog(body)
    assert page.attrs.get('data-n')==str(n) and page.attrs.get('data-seed')==str(seed),page.attrs
    assert page.values['request-token']==token,(page.values,token)
    expected=rows(n,seed)
    assert page.rows==expected,(len(page.rows),len(expected),next(((i,a,b) for i,(a,b) in enumerate(zip(page.rows,expected)) if a!=b),None))
    checksum=sum(int(r[2][:-3])+int(r[3]) for r in expected)
    assert int(page.values['checksum'])==checksum,(page.values,checksum)
    assert page.counters==int(interactive),(page.counters,interactive)
    stamp=int(page.values['render-stamp'])
    assert stamp==0 if static else abs(stamp-int(time.time()*1000))<60000,stamp
    return page


def get(port,path):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=30)
    try:
        start=time.perf_counter_ns();connection.request('GET',path,headers={'Accept-Encoding':'identity'})
        response=connection.getresponse();ttfb=time.perf_counter_ns()-start;body=response.read();total=time.perf_counter_ns()-start
        return response.status,dict(response.getheaders()),body,ttfb,total
    finally:connection.close()


def verify_server(port):
    checked=0
    for scenario in SCENARIOS:
        for n,seed,token in [(scenario['n'],17,'fixture'),(1,999,'<script>"café" & tea'),(17,2,'changed-token')]:
            path=scenario['path']+'?'+urlencode({'n':n,'seed':seed,'token':token})
            status,headers,body,_,_=get(port,path)
            assert status==200 and 'text/html' in headers.get('Content-Type',headers.get('content-type','')),(status,body[:500])
            assert 'no-store' in headers.get('Cache-Control',headers.get('cache-control','')),(scenario,headers)
            validate(body,n,seed,token,scenario['interactive']);checked+=1
    first=Catalog(get(port,'/?token=freshness')[2]);time.sleep(.03);second=Catalog(get(port,'/?token=freshness')[2])
    assert int(second.values['render-stamp'])>int(first.values['render-stamp']),'Response appears cached'
    return checked
