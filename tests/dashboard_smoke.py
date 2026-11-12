"""Real login, CSRF, per-user authorization and durable concurrent CRUD."""
import base64
import concurrent.futures
import hashlib
import hmac
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode
from tests.http_smoke import running,free_port
from tests.features_smoke import call

ROOT=Path(__file__).resolve().parents[1]
def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
def signed(subject,key,issued=None):
    """A v1 token (no issue time, still accepted) or, with `issued`, a v2 token."""
    # Use exactly 32 bytes, independently of the implementation's CSPRNG.
    csrf=b64(b'0123456789abcdef0123456789abcdef')
    expiry=int(time.time()*1000)+3600000
    body=(f'v2.{b64(subject.encode())}.{csrf}.{issued}.{expiry}' if issued is not None
          else f'v1.{b64(subject.encode())}.{csrf}.{expiry}')
    return body+'.'+b64(hmac.new(key.encode(),body.encode(),hashlib.sha256).digest()),csrf

def main(binary=None):
    binary=Path(binary or ROOT/'build/dashboard/server').resolve()
    with tempfile.TemporaryDirectory(prefix='neokit-dashboard-') as directory:
        key='test-session-key-32-characters-long-only-for-tests'
        salt=b'0123456789abcdef';password='correct-horse-test-password'
        encoded='p1.600000.'+b64(salt)+'.'+b64(hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000))
        port=free_port();origin=f'http://127.0.0.1:{port}'
        environment={'NEOKIT_SESSION_SECRET':key,'NEOKIT_ORIGIN':origin,'NEOKIT_ADMIN_PASSWORD_HASH':encoded,'NEOKIT_DATA_DIR':directory}
        with running(binary,2,environment,port) as port:
            assert call(port,'/dashboard')[0]==303
            status,headers,body=call(port,'/login');assert status==200
            cookie=headers['Set-Cookie'].split(';')[0]
            assert 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Lax' in headers['Set-Cookie']
            csrf=re.search(rb'name="_csrf" value="([^"]+)"',body)[1].decode()
            login=urlencode({'_csrf':csrf,'username':'admin','password':password})
            assert call(port,'/login','POST',login,{'Cookie':cookie,'Origin':'https://evil.test','Content-Type':'application/x-www-form-urlencoded'})[0]==403
            start=time.monotonic()
            status,headers,body=call(port,'/login','POST',login,{'Cookie':cookie,'Origin':origin,'Content-Type':'application/x-www-form-urlencoded'})
            assert status==303,(status,body)
            print(f'PASS 600000-round native password login ({time.monotonic()-start:.3f}s)',flush=True)
            cookie=headers['Set-Cookie'].split(';')[0]
            _,_,body=call(port,'/dashboard',headers={'Cookie':cookie})
            csrf=re.search(rb'name="_csrf" value="([^"]+)"',body)[1].decode()
            headers={'Cookie':cookie,'Origin':origin,'X-CSRF-Token':csrf,'Content-Type':'application/json'}
            assert call(port,'/api/tasks','POST','{"title":"blocked"}',{**headers,'Origin':'https://evil.test'})[0]==403
            assert call(port,'/api/tasks','POST','{"title":"blocked"}',{**headers,'X-CSRF-Token':'bad'})[0]==403
            status,_,result=call(port,'/api/tasks','POST',json.dumps({'title':'<script> café & test'}),headers)
            assert status==201,(status,result)
            task=json.loads(result)['id']
            status,_,body=call(port,'/dashboard',headers={'Cookie':cookie})
            assert b'&lt;script&gt; caf' in body and b'<script> caf' not in body
            other,other_csrf=signed('other-user',key)
            other_headers={'Cookie':'neokit_session='+other,'Origin':origin,'X-CSRF-Token':other_csrf,'Content-Type':'application/json'}
            assert call(port,'/api/tasks/'+task,'PATCH','{"title":"stolen"}',other_headers)[0]==404
            assert task.encode() not in call(port,'/dashboard',headers={'Cookie':'neokit_session='+other})[2]
            assert call(port,'/api/tasks/'+task,'PATCH','{"title":"Updated"}',headers)[0]==200
            updated=json.loads((Path(directory)/'tasks.json').read_text())['tasks']
            assert next(value for value in updated if value['id']==task)['title']=='Updated'
            def create(i):
                status,_,body=call(port,'/api/tasks','POST',json.dumps({'title':f'Concurrent {i}'}),headers)
                assert status==201,(status,body)
                return json.loads(body)['id']
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:ids=list(executor.map(create,range(24)))
            assert len(set(ids))==24
            stored=json.loads((Path(directory)/'tasks.json').read_text())
            assert stored['schema']==1 and len(stored['tasks'])==25
            assert call(port,'/api/tasks/'+task,'DELETE','{}',headers)[0]==200
            assert call(port,'/api/tasks/'+task,'DELETE','{}',headers)[0]==404
            assert call(port,'/dashboard',headers={'Cookie':cookie+'broken'})[0]==303
            logout=urlencode({'_csrf':csrf})
            status,returned,_=call(port,'/logout','POST',logout,{'Cookie':cookie,'Origin':origin,'Content-Type':'application/x-www-form-urlencoded'})
            assert status==303 and 'Max-Age=0' in returned['Set-Cookie']
            # Logout revokes the session itself, so a copy of the cookie stops working.
            assert call(port,'/dashboard',headers={'Cookie':cookie})[0]==303
            assert call(port,'/api/tasks/'+ids[0],'PATCH','{"title":"after logout"}',headers)[0]==401
            # One successful login is already counted; ten attempts a minute per client.
            status,returned,body=call(port,'/login');assert status==200
            anonymous=returned['Set-Cookie'].split(';')[0]
            form={'Cookie':anonymous,'Origin':origin,'Content-Type':'application/x-www-form-urlencoded'}
            wrong=urlencode({'_csrf':re.search(rb'name="_csrf" value="([^"]+)"',body)[1].decode(),'username':'admin','password':'wrong-password'})
            codes=[call(port,'/login','POST',wrong,form)[0] for _ in range(10)]
            assert codes==[401]*9+[429],codes
            status,returned,_=call(port,'/login','POST',wrong,{**form,'X-Forwarded-For':'203.0.113.9'})
            assert status==429 and returned['Retry-After']=='60',(status,returned)
        with running(binary,1,{**environment,'NEO_TRUSTED_PROXIES':'127.0.0.1'}) as restarted:
            assert call(restarted,'/dashboard',headers={'Cookie':cookie})[0]==303
            fresh,_=signed('admin',key,int(time.time()*1000))
            status,_,body=call(restarted,'/dashboard',headers={'Cookie':'neokit_session='+fresh})
            assert status==200 and all(identifier.encode() in body for identifier in ids)
            assert call(restarted,'/login','POST',wrong,form)[0]==429
            assert call(restarted,'/login','POST',wrong,{**form,'X-Forwarded-For':'203.0.113.9'})[0]==401
        print('PASS real login/logout with session revocation, per-client login limits behind a trusted proxy, CSRF, escaping, '
              'cross-user authorization, 24 concurrent durable writes across two workers and restart persistence')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path)
    main(parser.parse_args().binary)
