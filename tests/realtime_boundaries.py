"""Exact large SSE replay across callback byte budgets, including reset."""
import argparse,json,tempfile
from pathlib import Path
from tests.support import server,port
from tests.realtime import Events,request,quiet
parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--newlines',type=int,default=3900);args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='neokit-event-boundaries-') as temporary:
    number=port()
    with server([str(args.binary.resolve()),str(number)],{},number,Path(temporary)/'server.log'):
        anchor=Events(number);assert anchor.next()['event']=='neokit-ready'
        assert request(number,'/publish','POST',b'{"counter":0}')[0]==200
        initial=anchor.message();anchor.close();quiet(number)
        for counter in range(1,9):
            wire=b'{'+b'\n'*args.newlines+f'"counter":{counter},"text":"東京 café"}}'.encode()
            assert request(number,'/publish','POST',wire)[0]==200
        replay=Events(number,headers={'Last-Event-ID':initial['id']})
        try:
            events=[replay.message() for _ in range(8)]
            assert [v['data'] for v in events]==[{'counter':i,'text':'東京 café'} for i in range(1,9)]
            assert len({v['id'] for v in events})==8
        finally:replay.close()
        quiet(number)
        future=initial['id'].split('.')[0]+'.999999'
        reset=Events(number,headers={'Last-Event-ID':future})
        try:
            assert reset.next()['event']=='neokit-ready'
            assert reset.next()['event']=='neokit-reset'
            assert reset.message()['data']=={'counter':8,'text':'東京 café'}
        finally:reset.close()
        state=quiet(number);assert state['frame_entries']<=64
        print(f'PASS {args.newlines} newlines/event: eight exact replayed UTF-8 events across bounded callback chunks, reset/latest, cleanup and bounded cache',flush=True)
