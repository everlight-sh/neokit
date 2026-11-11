"""Run the dashboard integration in a nonroot scratch Linux image.

Build build/linux/dashboard/server and migrate first. Requires a running
Docker daemon of that architecture. No package installation occurs.
"""
import argparse,hashlib,json,shlex,shutil,subprocess,tempfile,uuid
from pathlib import Path
from tests import dashboard_smoke

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'build/linux/dashboard')
    parser.add_argument('--platform',choices=['linux/arm64','linux/amd64'])
    args=parser.parse_args();out=args.out.resolve()
    assert (out/'server').read_bytes()[:4]==b'\x7fELF','Expected a Linux ELF executable'
    work=ROOT/'build/linux/tmp';work.mkdir(parents=True,exist_ok=True)
    tag='neokit-verify-'+uuid.uuid4().hex[:12]
    platform_flags=['--platform',args.platform] if args.platform else []
    identity={'image_tag':tag,'docker_host_architecture':subprocess.check_output(['docker','info','--format','{{.Architecture}}'],text=True).strip(),'platform':args.platform,
              'binary_sha256':hashlib.sha256((out/'server').read_bytes()).hexdigest(),
              'profile':'scratch, Docker init, nonroot host UID, read-only root, all capabilities dropped, no-new-privileges; host-mounted disposable data',
              'checks':'600000-round password login; CSRF and user authorization; 24 concurrent writes, two workers, restart persistence'}
    original=tempfile.tempdir;tempfile.tempdir=str(work)
    try:
        with tempfile.TemporaryDirectory(prefix='image-',dir=work) as temporary:
            context=Path(temporary)
            for name in ('server','migrate'):shutil.copy2(out/name,context/name)
            (context/'Dockerfile').write_text('FROM scratch\nCOPY server /server\nCOPY migrate /migrate\nUSER 65532:65532\nENTRYPOINT ["/server"]\n')
            subprocess.run(['docker','build',*platform_flags,'-q','-t',tag,str(context)],check=True)
            identity['image_id']=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
            runner=context/'runner'
            runner.write_text('#!/bin/sh\nset -eu\nport=$1\nexec docker run --rm --init --read-only --cap-drop=ALL --security-opt=no-new-privileges '+
              '--user "$(id -u):$(id -g)" -p "127.0.0.1:$port:$port" -v "$NEOKIT_DATA_DIR:$NEOKIT_DATA_DIR" '+
              '-e NEO_THREADS -e NEOKIT_DATA_DIR -e NEOKIT_ORIGIN -e NEOKIT_SESSION_SECRET -e NEOKIT_ADMIN_PASSWORD_HASH '+shlex.join(platform_flags)+' '+shlex.quote(tag)+' "$port"\n')
            runner.chmod(0o755);dashboard_smoke.main(runner)
        identity['passed']=True
        (out/'verification.json').write_text(json.dumps(identity,indent=2)+'\n')
        print('PASS Linux scratch integration; no shell, libc or dynamic loader in application image')
    finally:
        tempfile.tempdir=original
        subprocess.run(['docker','image','rm',tag],stdout=subprocess.DEVNULL,check=False)

if __name__=='__main__':main()
