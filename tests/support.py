"""Free ports and a supervised server process for the protocol tests."""
import contextlib
import os
import secrets
import signal
import socket
import subprocess
import time


def port():
    for _ in range(128):
        number=15000+secrets.randbelow(15000)
        with socket.socket() as sock:
            try:sock.bind(('0.0.0.0',number))
            except OSError:continue
            return number
    raise RuntimeError('No free test listener port')


@contextlib.contextmanager
def server(command,environment,number,log):
    """Start one single-worker server, wait until it accepts, and yield it with its startup time in ms."""
    with log.open('w') as output:
        start=time.monotonic_ns()
        process=subprocess.Popen(command,env={**os.environ,**environment,'NEO_THREADS':'1'},stdout=output,stderr=output,start_new_session=True)
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError(log.read_text())
                try:
                    with socket.create_connection(('127.0.0.1',number),timeout=.05):break
                except OSError:time.sleep(.005)
            else:raise RuntimeError('Server startup timeout')
            yield process,(time.monotonic_ns()-start)/1e6
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=15)
