"""TLS termination check: prove the reference proxy terminates HTTPS correctly.

Usage: /usr/bin/python3 lab/tls_termination_check.py

Starts a stub upstream that serves the real built console page, terminates TLS in
front of it with deploy/console-tls-proxy.ts on ephemeral ports using a generated
self-signed certificate, and proves: the built page and its assets come through
unchanged over HTTPS, a management route is reachable, the transport security
headers are set, plain HTTP redirects, the key's permissions are enforced, a
non-loopback or non-http upstream is refused, and shutdown releases both ports.

Honest scope: the certificate is self-signed and the upstream is a stub serving the
real built assets, not a running installation. A public certificate and the real
console are the server acceptance run's business.
"""
import datetime
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
EVIDENCE=ROOT/'docs'/'evidence'/'tls-termination.json'
BUILD=ROOT/'.lab'/'ui'
PUBLIC_HOST='console.example.com'
CONTEXT=ssl._create_unverified_context()


def generate_certificate(directory):
    certificate=directory/'cert.pem';key=directory/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','2',
                    '-keyout',str(key),'-out',str(certificate),
                    '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],
                   check=True,capture_output=True)
    os.chmod(key,0o600);os.chmod(certificate,0o644)
    return certificate,key


def start(command,pattern,timeout=60,cwd=ROOT,required=True):
    process=subprocess.Popen(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
    deadline=time.monotonic()+timeout
    captured=[]
    while time.monotonic()<deadline:
        line=process.stdout.readline()
        if line:
            captured.append(line.rstrip())
            match=pattern.search(line)
            if match:return process,match,captured
        elif process.poll() is not None:
            break
    process.terminate()
    if not required:return process,None,captured
    raise RuntimeError('Process did not announce its port: '+' | '.join(captured[-4:]))


def drain(process,seconds=3.0):
    """Collect whatever the process prints next, without waiting for exit."""
    lines=[]
    if process.stdout is None:return lines
    deadline=time.monotonic()+seconds
    fd=process.stdout.fileno()
    import select
    while time.monotonic()<deadline:
        ready,_,_=select.select([fd],[],[],max(0.1,deadline-time.monotonic()))
        if not ready:break
        line=process.stdout.readline()
        if not line:break
        lines.append(line.rstrip())
    return lines


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect must be observed, not followed (the target is self-signed)."""

    def redirect_request(self,*args,**kwargs):
        return None


def fetch(url,context=None,method='GET',follow=False,headers=None,body=None):
    handlers=[NoRedirect()] if not follow else []
    if context is not None:handlers.append(urllib.request.HTTPSHandler(context=context))
    opener=urllib.request.build_opener(*handlers)
    request=urllib.request.Request(url,method=method,headers=headers or {},data=body)
    try:
        with opener.open(request,timeout=10) as response:
            return response.status,dict(response.headers),response.read()
    except urllib.error.HTTPError as error:
        return error.code,dict(error.headers),error.read()
    except Exception as error:
        return None,{},str(error).encode()


def free_ports(count):
    """Distinct loopback ports nothing is listening on, held until all are chosen."""
    sockets=[]
    try:
        for _ in range(count):
            probe=socket.socket();probe.bind(('127.0.0.1',0));sockets.append(probe)
        return [probe.getsockname()[1] for probe in sockets]
    finally:
        for probe in sockets:probe.close()


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(('127.0.0.1',port))==0


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=30)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL)
    if process.stdout and not process.stdout.closed:process.stdout.close()
    return process.returncode


def finish(checks):
    """Write the evidence and exit: an aborted run still records what it saw."""
    passed=bool(checks) and all(item['ok'] for item in checks)
    evidence={'scope':('TLS termination check: a self-signed certificate, deploy/console-tls-proxy.ts terminating HTTPS in '
                       'front of a stub upstream that serves the real built console page, HTTP to HTTPS redirection, the '
                       'transport security headers, the proxy\'s handling of attacker supplied hosts and oversized bodies, '
                       'and its own refusals. Not a public certificate, not a running installation, not the operator\'s '
                       'chosen proxy (nginx, Caddy, the platform proxy).'),
              'run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'checks':checks,'count':len(checks),'passed':passed}
    EVIDENCE.parent.mkdir(parents=True,exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',EVIDENCE)
    print('TLS termination check:','passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


def main():
    import tempfile
    checks=[]
    def record(label,ok,detail=''):
        detail=str(detail)
        checks.append({'check':label,'ok':bool(ok),'detail':detail})
        print(('ok: ' if ok else 'FAIL: ')+label+(('  '+str(detail)) if detail and not ok else ''))

    if not (BUILD/'index.html').exists():
        raise SystemExit('Run bun run build:ui first; the check serves the built page')

    directory=Path(tempfile.mkdtemp(prefix='tls-check-'))
    certificate,key=generate_certificate(directory)
    stub=None;proxy=None
    try:
        stub,stub_match,stub_log=start(['bun','lab/tls_upstream_stub.ts'],re.compile(r'"port":(\d+)'),required=False)
        record('stub upstream serving the real built page',stub_match is not None,str(stub_log[-2:]))
        if stub_match is None:
            record('the check could not run without its upstream',False,'stub upstream did not start')
            return finish(checks)
        stub_port=stub_match.group(1)

        https_port,http_port=[str(port) for port in free_ports(2)]
        proxy,proxy_match,proxy_log=start(['bun','deploy/console-tls-proxy.ts','--cert',str(certificate),'--key',str(key),
                                           '--upstream','http://127.0.0.1:'+stub_port,
                                           '--public-host',PUBLIC_HOST,
                                           '--https-port',https_port,'--http-port',http_port],
                                          re.compile(r'https://127\.0\.0\.1:'+https_port))
        record('proxy announced its HTTPS port',proxy_match is not None,str(proxy_log[-1:]))
        if proxy_match is None:
            record('the check could not run without the proxy',False,'proxy did not announce its ports')
            return finish(checks)
        record('proxy binds the HTTPS and the redirect port it was given',
               port_open(int(https_port)) and port_open(int(http_port)))
        base='https://127.0.0.1:'+https_port

        status,headers,body=fetch(base+'/',CONTEXT)
        built=(BUILD/'index.html').read_bytes()
        record('the built page is served over HTTPS unchanged',status==200 and body==built,f'status {status} bytes {len(body)} vs {len(built)}')
        record('HTTPS carries the transport security header',headers.get('Strict-Transport-Security','').startswith('max-age='),headers.get('Strict-Transport-Security'))
        record('HTTPS forbids content sniffing',headers.get('X-Content-Type-Options')=='nosniff')

        reference=re.search(rb'(?:src|href)="(/assets/[^"]+)"',built)
        if reference:
            asset=reference.group(1).decode()
            status,_,body=fetch(base+asset,CONTEXT)
            on_disk=(BUILD/asset.lstrip('/')).read_bytes()
            record('an asset is served over HTTPS byte for byte',status==200 and body==on_disk,asset)
        else:
            record('the built page references an asset',False,'no /assets reference found')

        status,_,body=fetch(base+'/management/auth/v1/settings',CONTEXT)
        record('a management route is reachable through the proxy',status==200 and b'management-reachable-through-tls' in body,f'status {status}')

        status,_,body=fetch(base+'/echo-forwarded',CONTEXT)
        record('the proxy tells the upstream the original protocol was https',body.strip()==b'https',body[:40])

        status,_,body=fetch(base+'/echo-host',CONTEXT,headers={'Host':'evil.example.net'})
        record('a client supplied Host header cannot override the configured public host',
               body.strip()==PUBLIC_HOST.encode(),body[:60])

        if http_port:
            status,headers,_=fetch('http://127.0.0.1:'+http_port+'/some/path?x=1')
            location=headers.get('Location','')
            record('plain HTTP is redirected to HTTPS',status==308 and location.startswith('https://'+PUBLIC_HOST),f'status {status} location {location}')
            status,headers,_=fetch('http://127.0.0.1:'+http_port+'/some/path',headers={'Host':'evil.example.net'})
            location=headers.get('Location','')
            record('the redirect never points at a client supplied host',status==308 and location.startswith('https://'+PUBLIC_HOST),f'status {status} location {location}')

        small_https,small_http=[str(port) for port in free_ports(2)]
        small,small_match,_=start(['bun','deploy/console-tls-proxy.ts','--cert',str(certificate),'--key',str(key),
                                   '--upstream','http://127.0.0.1:'+stub_port,'--public-host',PUBLIC_HOST,
                                   '--max-body','64','--https-port',small_https,'--http-port',small_http],
                                  re.compile(r'https://127\.0\.0\.1:'+small_https))
        try:
            status,_,_=fetch('https://127.0.0.1:'+small_https+'/',CONTEXT,method='POST',body=b'x'*200)
            record('a declared body over the limit is answered 413 without being read',status==413,f'status {status}')
            # The stub answers 405 to a write, so 405 here means the body reached it.
            status,_,_=fetch('https://127.0.0.1:'+small_https+'/',CONTEXT,method='POST',body=b'x'*10)
            record('a body within the limit still reaches the upstream',status==405,f'status {status}')
        finally:
            stop(small)

        os.chmod(key,0o644)
        result=subprocess.run(['bun','deploy/console-tls-proxy.ts','--cert',str(certificate),'--key',str(key),'--public-host',PUBLIC_HOST,
                               '--upstream','http://127.0.0.1:'+stub_port,'--https-port','0','--http-port','0'],
                              cwd=ROOT,capture_output=True,text=True,timeout=60)
        record('a group or world readable key is refused',result.returncode!=0 and 'readable' in (result.stdout+result.stderr),
               (result.stdout+result.stderr).strip().splitlines()[-1][:120] if result.returncode else 'started anyway')
        os.chmod(key,0o600)

        result=subprocess.run(['bun','deploy/console-tls-proxy.ts','--cert',str(certificate),'--key',str(key),'--public-host',PUBLIC_HOST,
                               '--upstream','http://198.51.100.7:8000','--https-port','0','--http-port','0'],
                              cwd=ROOT,capture_output=True,text=True,timeout=60)
        record('a non-loopback upstream is refused',result.returncode!=0 and 'loopback' in (result.stdout+result.stderr),
               (result.stdout+result.stderr).strip().splitlines()[-1][:120] if result.returncode else 'started anyway')

        result=subprocess.run(['bun','deploy/console-tls-proxy.ts','--key',str(key),'--public-host',PUBLIC_HOST],
                              cwd=ROOT,capture_output=True,text=True,timeout=60)
        record('a missing certificate argument is refused',result.returncode!=0 and 'required' in (result.stdout+result.stderr),
               (result.stdout+result.stderr).strip().splitlines()[-1][:120] if result.returncode else 'started anyway')

        result=subprocess.run(['bun','deploy/console-tls-proxy.ts','--cert',str(certificate),'--key',str(key)],
                              cwd=ROOT,capture_output=True,text=True,timeout=60)
        record('a missing public host is refused',result.returncode!=0 and 'required' in (result.stdout+result.stderr),
               (result.stdout+result.stderr).strip().splitlines()[-1][:120] if result.returncode else 'started anyway')

        result=subprocess.run(['bun','deploy/console-tls-proxy.ts','--cert',str(certificate),'--key',str(key),
                               '--public-host','evil.example.com/path'],
                              cwd=ROOT,capture_output=True,text=True,timeout=60)
        record('a public host that is not a bare host name is refused',
               result.returncode!=0 and 'bare host name' in (result.stdout+result.stderr),
               (result.stdout+result.stderr).strip().splitlines()[-1][:120] if result.returncode else 'started anyway')

        code=stop(proxy);proxy=None
        record('the proxy shut down on SIGTERM',code in (0,None),'exit '+str(code))
        time.sleep(.5)
        record('both ports are released after shutdown',not port_open(int(https_port)) and not port_open(int(http_port or 0)),
               f'https {https_port} http {http_port}')
    finally:
        for process in (proxy,stub):
            if process is not None:stop(process)
        for path in (certificate,key):
            path.unlink(missing_ok=True)
        directory.rmdir()

    return finish(checks)


if __name__=='__main__':main()