"""One retained psql connection for fencing, SQL replay and validation.

The connection never reconnects. A bounded nonblocking transport drains stdout
while writing COPY data and refuses diagnostics through a bounded private pipe.
The archive planner must already have removed or refused psql commands.
"""
import os
import selectors
import subprocess
import tempfile
import time
import uuid


class SessionError(RuntimeError):
    pass


class HeldSession:
    def __init__(self, argv, *, timeout=600, output_limit=1024 * 1024):
        self.timeout = timeout
        self.output_limit = output_limit
        self.diagnostics = None
        self.had_diagnostics = False
        # Never include these bytes in a normal error or log. A synthetic
        # verifier may explicitly retain them in its owned private evidence.
        self.private_diagnostic = b''
        self.process = None
        self.closed = False
        try:
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE)
            self.diagnostics = self.process.stderr
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            os.set_blocking(self.diagnostics.fileno(), False)
        except BaseException:
            if self.diagnostics is not None:
                self.diagnostics.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close(check=kind is None)

    def execute(self, query):
        with tempfile.TemporaryFile() as source:
            source.write(query.encode('utf-8'))
            source.seek(0)
            return self.replay(source)

    def replay(self, source):
        if self.closed or self.process.poll() is not None:
            raise SessionError('Retained restore session is unavailable')
        marker = '__sbarbase_restore_' + uuid.uuid4().hex + '__'
        ending = ('\nSELECT \'' + marker + '\';\n').encode('ascii')
        source_done, ending_done = False, False
        pending = b''
        received = bytearray()
        deadline = time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ, 'read')
            selector.register(self.process.stdin, selectors.EVENT_WRITE, 'write')
            selector.register(self.diagnostics, selectors.EVENT_READ, 'diagnostics')
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SessionError('Retained restore session timed out')
                events = selector.select(min(remaining, .5))
                if not events and self.process.poll() is not None:
                    raise SessionError('Retained restore session exited before confirmation')
                for key, mask in events:
                    if key.data == 'diagnostics':
                        try:
                            chunk = os.read(self.diagnostics.fileno(), 4096)
                        except BlockingIOError:
                            continue
                        if chunk:
                            self.had_diagnostics = True
                            if not self.private_diagnostic:
                                self.private_diagnostic = chunk[:4096]
                            self.process.kill()
                            raise SessionError('Retained restore session emitted diagnostics')
                        selector.unregister(self.diagnostics)
                    elif key.data == 'write':
                        if not pending and not source_done:
                            pending = source.read(65536)
                            if isinstance(pending, str):
                                raise SessionError('Restore SQL transport requires bytes')
                            source_done = not pending
                        if not pending and source_done and not ending_done:
                            pending, ending_done = ending, True
                        if pending:
                            try:
                                written = os.write(self.process.stdin.fileno(), pending)
                                pending = pending[written:]
                            except BlockingIOError:
                                continue
                            except BrokenPipeError:
                                raise SessionError('Retained restore session refused SQL') from None
                        if not pending and ending_done:
                            selector.unregister(self.process.stdin)
                    else:
                        try:
                            chunk = os.read(self.process.stdout.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            raise SessionError('Retained restore session closed without confirmation')
                        received.extend(chunk)
                        if len(received) > self.output_limit:
                            raise SessionError('Restore validation output exceeds supported size')
                        needle = marker.encode('ascii') + b'\n'
                        position = received.find(needle)
                        if position != -1:
                            if position and received[position - 1] != 10 \
                                    or position + len(needle) != len(received) or not ending_done or pending:
                                raise SessionError('Restore session confirmation is ambiguous')
                            try:
                                diagnostics = os.read(self.diagnostics.fileno(), 4096)
                            except BlockingIOError:
                                diagnostics = b''
                            if diagnostics:
                                self.had_diagnostics = True
                                if not self.private_diagnostic:
                                    self.private_diagnostic = diagnostics[:4096]
                                self.process.kill()
                                raise SessionError('Retained restore session emitted diagnostics')
                            try:
                                return received[:position].decode('utf-8').strip()
                            except UnicodeDecodeError:
                                raise SessionError('Restore validation output is not UTF-8') from None

    def close(self, *, check=True):
        if self.closed:
            return
        self.closed = True
        process = self.process
        failure = None
        try:
            if process is not None:
                process.stdin.close()
                deadline = time.monotonic() + 5
                while process.poll() is None:
                    try:
                        diagnostics = os.read(self.diagnostics.fileno(), 4096)
                    except BlockingIOError:
                        diagnostics = b''
                    if diagnostics:
                        self.had_diagnostics = True
                        if not self.private_diagnostic:
                            self.private_diagnostic = diagnostics[:4096]
                        process.kill()
                        break
                    if time.monotonic() >= deadline:
                        process.kill()
                        break
                    time.sleep(.01)
                code = process.wait(timeout=5)
                try:
                    diagnostics = os.read(self.diagnostics.fileno(), 4096)
                    if diagnostics:
                        self.had_diagnostics = True
                        if not self.private_diagnostic:
                            self.private_diagnostic = diagnostics[:4096]
                except BlockingIOError:
                    pass
                if code != 0 or self.had_diagnostics:
                    failure = SessionError('Retained restore session did not finish cleanly')
        finally:
            if process is not None:
                process.stdout.close()
            self.diagnostics.close()
        if check and failure is not None:
            raise failure
