import http.server
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from baidu_index_tool.transport import (
    NetworkError, TLSCertificateError, UrlLibTransport, create_tls_context,
)


class TLSContextTests(unittest.TestCase):
    def test_desktop_uses_system_trust_without_default_ca_path(self):
        if sys.platform not in ('darwin', 'win32'):
            self.skipTest('desktop OS trust check')
        with patch('baidu_index_tool.transport.ssl.create_default_context') as factory:
            context = create_tls_context()
        factory.assert_not_called()
        self.assertEqual(type(context).__module__, 'truststore._api')
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_explicit_ca_is_respected(self):
        with patch('baidu_index_tool.transport.ssl.create_default_context') as factory:
            context = create_tls_context('/test/ca.pem')
        factory.assert_called_once_with(cafile='/test/ca.pem')
        self.assertIs(context, factory.return_value)

    def test_linux_uses_host_openssl_store(self):
        with patch('baidu_index_tool.transport.sys.platform', 'linux'), \
                patch('baidu_index_tool.transport.ssl.create_default_context') as factory:
            self.assertIs(create_tls_context(), factory.return_value)
        factory.assert_called_once_with()

    def test_missing_native_component_is_not_silently_ignored(self):
        with patch('baidu_index_tool.transport.sys.platform', 'darwin'), \
                patch.dict(sys.modules, {'truststore': None}):
            with self.assertRaisesRegex(NetworkError, '证书组件缺失'):
                create_tls_context()

    def test_get_certificate_error_has_specific_type(self):
        transport = UrlLibTransport()
        with patch('baidu_index_tool.transport.urlopen', side_effect=URLError(ssl.SSLCertVerificationError('untrusted'))):
            with self.assertRaisesRegex(TLSCertificateError, 'HTTPS 证书验证失败'):
                transport.get_json('https://localhost/', {}, {})

    def test_post_certificate_error_has_specific_type(self):
        transport = UrlLibTransport()
        with patch('baidu_index_tool.transport.urlopen', side_effect=ssl.SSLCertVerificationError('untrusted')):
            with self.assertRaises(TLSCertificateError):
                transport.post_json('https://localhost/', {}, {})


@unittest.skipUnless(shutil.which('openssl'), 'openssl required for local TLS fixture')
class TLSIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cert = root / 'localhost.pem'
        key = root / 'localhost.key'
        config = root / 'openssl.cnf'
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n'
                          '[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\n'
                          'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cls.cert), '-days', '1', '-config', str(config)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'{"status":0}')

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.do_GET()

            def log_message(self, *args):
                pass

        class TLSServer(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # Negative verification tests close the handshake deliberately.
                if isinstance(sys.exc_info()[1], (ConnectionResetError, ssl.SSLError, BrokenPipeError)):
                    return
                super().handle_error(request, client_address)

        cls.server = TLSServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'https://localhost:%d/' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.directory.cleanup()

    def test_trusted_get_and_post(self):
        transport = UrlLibTransport(ca_file=str(self.cert))
        self.assertEqual(transport.get_json(self.url, {}, {}), {'status': 0})
        self.assertEqual(transport.post_json(self.url, {'input': 'fixture'}, {}), {'status': 0})
        self.assertEqual(transport.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(transport.context.check_hostname)

    def test_self_signed_certificate_still_rejected(self):
        with self.assertRaises(TLSCertificateError):
            UrlLibTransport(timeout=3).get_json(self.url, {}, {})

    def test_hostname_mismatch_still_rejected(self):
        with self.assertRaises(TLSCertificateError):
            UrlLibTransport(ca_file=str(self.cert)).get_json(self.url.replace('localhost', '127.0.0.1'), {}, {})


if __name__ == '__main__':
    unittest.main()
