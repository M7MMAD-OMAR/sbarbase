"""Bounded synthetic HTTP recorder for the owned internal fixture network."""

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def main():
    marker = os.environ['SBARBASE_FIXTURE_ID']
    if not re.fullmatch(r'identity-[A-Za-z0-9-]+', marker):
        raise ValueError('Invalid fixture marker')
    receipts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != '/events':
                self.reply(404, {'error': 'fixture path required'})
                return
            self.reply(200, {'marker': marker, 'events': receipts})

        def do_POST(self):
            if self.path != '/fixture':
                self.reply(404, {'error': 'fixture path required'})
                return
            if (self.headers.get('Transfer-Encoding') is not None
                    or self.headers.get('Content-Type') != 'application/json'
                    or len(self.headers.get_all('Content-Length', [])) != 1):
                self.reply(400, {'error': 'strict JSON framing required'})
                return
            try:
                raw_length = self.headers['Content-Length']
                if not re.fullmatch(r'[1-9][0-9]{0,3}', raw_length):
                    raise ValueError('length')
                length = int(raw_length)
                if length > 4096:
                    raise ValueError('length')
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result:
                            raise ValueError('duplicate field')
                        result[key] = value
                    return result
                value = json.loads(self.rfile.read(length).decode('utf-8'),
                                   object_pairs_hook=pairs,
                                   parse_constant=lambda _value: (_ for _ in ()).throw(ValueError('constant')))
                if (not isinstance(value, dict) or set(value) != {'marker', 'sequence', 'kind'}
                        or value['marker'] != marker or type(value['sequence']) is not int
                        or not 1 <= value['sequence'] <= 128
                        or value['kind'] not in ('cron', 'explicit')):
                    raise ValueError('synthetic payload')
            except (ValueError, UnicodeError, TypeError):
                self.reply(400, {'error': 'invalid synthetic JSON'})
                return
            if len(receipts) >= 128:
                self.reply(507, {'error': 'receipt bound reached'})
                return
            receipt = dict(value, received_epoch=time.time())
            receipts.append(receipt)
            self.reply(200, value)

    class Server(HTTPServer):
        request_queue_size = 8

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

    Server(('0.0.0.0', 8080), Handler).serve_forever(poll_interval=.2)


if __name__ == '__main__':
    main()
