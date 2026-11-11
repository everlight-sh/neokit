"""Independent HTTP/SSE peer: replay, auth, heartbeats, bounds and shutdown."""
import argparse,concurrent.futures,http.client,json,os,signal,socket,subprocess,tempfile,time
from pathlib import Path
from bench.parity_performance import server,port
ROOT=Path(__file__).resolve().parents[1]

def request(number,path,method='GET',body=None,headers=None):
    connection=http.client.HTTPConnection('127.0.0.1',number,timeout=5)
    try:
        connection.request(method,path,body=body,headers=headers or {});reply=connection.getresponse();return reply.status,dict(reply.getheaders()),reply.read()
    finally:connection.close()

class Events:
    def __init__(self,number,path='/feed',headers=None,timeout=5):
        self.connection=http.client.HTTPConnection('127.0.0.1',number,timeout=timeout)
        self.connection.request('GET',path,headers={'Cookie':'session=allowed',**(headers or {})});self.reply=self.connection.getresponse()
        assert self.reply.status==200,self.reply.status
        assert self.reply.getheader('Content-Type')=='text/event-stream; charset=utf-8'
        assert self.reply.getheader('Transfer-Encoding')=='chunked' and self.reply.getheader('Content-Length') is None
        assert self.reply.getheader('X-Accel-Buffering')=='no' and self.reply.getheader('Cache-Control')=='no-store'
    def next(self,comments=False):
        event={};data=[]
        while True:
            line=self.reply.readline()
            if not line:raise EOFError('Event stream closed')
            line=line.decode('utf-8').rstrip('\r\n')
            if line.startswith(':') and comments:return {'comment':line}
            if not line:
                if data:event['data']=json.loads('\n'.join(data));return event
                event={};continue
            field,_,value=line.partition(':');value=value.removeprefix(' ')
            if field=='data':data.append(value)
            elif field in ('event','id'):event[field]=value
    def message(self):
        while True:
            event=self.next()
            if event.get('event') in ('message','dashboard'):return event
    def close(self):self.reply.close();self.connection.close()

def quiet(number):
    until=time.monotonic()+2
    while time.monotonic()<until:
        stats=json.loads(request(number,'/stats')[2])
        if all(stats[key]==0 for key in ['subscribers','tasks','captures']):return stats
        time.sleep(.01)
    raise AssertionError(stats)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,default=ROOT/'build/realtime/fixture/server');parser.add_argument('--out',type=Path);args=parser.parse_args();result={};streams=[]
    with tempfile.TemporaryDirectory(prefix='neokit-realtime-') as temporary:
        number=port()
        with server([str(args.binary.resolve()),str(number)],{},number,Path(temporary)/'server.log') as (process,_):
            assert request(number,'/feed')[0]==403
            assert request(number,'/feed','POST',headers={'Cookie':'session=allowed'})[0]==405
            head=request(number,'/feed','HEAD',headers={'Cookie':'session=allowed'});assert head[0]==200 and head[1]['Content-Type'].startswith('text/event-stream') and head[2]==b'';quiet(number)
            snapshot=request(number,'/feed/snapshot',headers={'Cookie':'session=allowed'});assert snapshot[0]==200 and json.loads(snapshot[2])=={'snapshot':True}
            idle=Events(number);streams.append(idle);assert idle.next()['event']=='neokit-ready'
            before=json.loads(request(number,'/stats')[2]);time.sleep(.8)
            after=json.loads(request(number,'/stats')[2]);assert after['callbacks']==before['callbacks'] and after['reads']==before['reads'],(before,after)
            assert request(number,'/publish-other','POST',b'{}')[0]==200;time.sleep(.03)
            unrelated=json.loads(request(number,'/stats')[2]);assert unrelated['callbacks']==before['callbacks'] and unrelated['reads']==before['reads']
            idle.close();streams.clear();quiet(number);result['idle_channel_reads']=0;result['idle_subscriber_callbacks']=0
            a=Events(number);b=Events(number);streams+=[a,b]
            def publish(value):
                response=request(number,'/publish','POST',json.dumps(value,ensure_ascii=False).encode(),{'Content-Type':'application/json'})
                assert response[0]==200,response;return json.loads(response[2])['sequence']
            publish({'counter':1,'text':'café <script>東京</script>'});first=a.message();second=b.message();assert first==second,(first,second)
            cached=json.loads(request(number,'/stats')[2]);assert cached['frame_misses']==1 and cached['frame_hits']==1 and cached['frame_entries']==1,cached
            result['shared_frame_encoding']='PASS one prepared frame for two subscribers'
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(request,number,'/wait');time.sleep(.03);publish({'counter':1,'coexist':True})
                first=a.message();assert first==b.message() and first['data']['coexist']
                assert not pending.done(),'Async loader should still be waiting while SSE is delivered'
                assert pending.result()[0]==200
            delays=[]
            for index in range(24):
                time.sleep([.007,.013,.021,.031][index%4]);began=time.monotonic();publish({'counter':index,'timed':True})
                first=a.message();assert first==b.message();delays.append((time.monotonic()-began)*1000)
            ordered=sorted(delays);p95=ordered[int(len(ordered)*.95)];assert p95<80 and ordered[len(ordered)//2]<30,delays
            result['arbitrary_publish_delivery_ms']={'p50':ordered[len(ordered)//2],'p95':p95,'max':max(delays),'samples':delays}
            a.close();b.close();streams.clear();quiet(number)
            publish({'counter':2});publish({'counter':3})
            replay=Events(number,'/feed?channel=untrusted',{'Last-Event-ID':first['id']});streams.append(replay)
            got=[replay.message(),replay.message()];assert [event['data']['counter'] for event in got]==[2,3]
            replay.close();streams.clear();quiet(number)
            for index in range(16):publish({'counter':1000+index})
            burst=Events(number,'/feed',{'Last-Event-ID':got[-1]['id']});streams.append(burst);began=time.monotonic()
            assert [burst.message()['data']['counter'] for _ in range(16)]==list(range(1000,1016))
            result['replay_16_events_seconds']=time.monotonic()-began;assert result['replay_16_events_seconds']<.2
            burst.close();streams.clear();quiet(number)
            for counter in range(4,145):publish({'counter':counter})
            gap=Events(number,'/feed',{'Last-Event-ID':first['id']});streams.append(gap)
            assert gap.next()['event']=='neokit-ready';assert gap.next()['event']=='neokit-reset';assert gap.message()['data']['counter']==144
            gap.close();streams.clear();quiet(number)
            for cursor in ['1.1',first['id'].split('.')[0]+'.999999']:
                stream=Events(number,'/feed',{'Last-Event-ID':cursor});streams.append(stream)
                assert stream.next()['event']=='neokit-ready';assert stream.next()['event']=='neokit-reset';assert stream.message()['data']['counter']==144
                stream.close();streams.clear()
            assert request(number,'/feed?after=bad',headers={'Cookie':'session=allowed'})[0]==400
            assert request(number,'/publish','POST',b'invalid')[0]==400
            assert request(number,'/publish','POST',json.dumps({'data':'x'*5000}))[0]==400
            heartbeat=Events(number,timeout=18);streams.append(heartbeat);heartbeat.message();before=json.loads(request(number,'/stats')[2]);began=time.monotonic()
            while 'comment' not in (event:=heartbeat.next(comments=True)):pass
            elapsed=time.monotonic()-began;assert 14<=elapsed<=17,(elapsed,event)
            after=json.loads(request(number,'/stats')[2]);assert after['reads']==before['reads'],'Heartbeat must not poll channel data'
            heartbeat.close();streams.clear();quiet(number);result['heartbeat_seconds']=elapsed
            slow=[]
            for _ in range(4):
                peer=socket.create_connection(('127.0.0.1',number));peer.sendall(b'GET /feed HTTP/1.1\r\nHost: localhost\r\nCookie: session=allowed\r\n\r\n');slow.append(peer)
            for counter in range(200):publish({'counter':counter,'data':'x'*3500})
            assert request(number,'/healthz')[0]==200
            for peer in slow:peer.close()
            quiet(number)
            hold=Events(number);streams.append(hold);began=time.monotonic();os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=2);result['shutdown_seconds']=time.monotonic()-began
            hold.close();streams.clear();assert result['shutdown_seconds']<1
            result['single_worker']='PASS auth/HEAD/methods, static-over-dynamic stream precedence, async loader/SSE coexistence, matching subscribers, replay, reset, UTF-8, heartbeat, bounds, slow readers, cleanup, shutdown'
        number=port()
        # server() deliberately fixes NEO_THREADS=1; start this owned process
        # explicitly to test cross-worker shared-memory publication.
        with (Path(temporary)/'multi.log').open('w') as log:
            process=subprocess.Popen([str(args.binary.resolve()),str(number)],env={**os.environ,'NEO_THREADS':'2'},stdout=log,stderr=log,start_new_session=True)
            try:
                until=time.monotonic()+5
                while True:
                    try:
                        if request(number,'/healthz')[0]==200:break
                    except OSError:
                        if time.monotonic()>until:raise
                        time.sleep(.01)
                streams=[Events(number),Events(number),Events(number)]
                streams.pop(0).close()
                for _ in range(20):assert request(number,'/healthz')[0]==200
                response=request(number,'/publish','POST',b'{"cross_worker":true}');assert response[0]==200
                messages=[stream.message() for stream in streams];assert messages[0]==messages[1] and messages[0]['data']['cross_worker']
                result['two_workers']='PASS matching events across forked workers after connection churn/slot compaction'
            finally:
                for stream in streams:stream.close()
                streams.clear();os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3)
    if args.out:args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':main()
