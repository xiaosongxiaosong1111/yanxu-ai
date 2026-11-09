"""Pinned HTTPS must emit the same Host authority as a standard HTTPS client."""
import unittest
from unittest.mock import Mock, patch

from collectors.discovery import _PinnedConnection


class PinnedHTTPHeaderTests(unittest.TestCase):
    def test_default_tls_port_is_not_sent_as_a_nondefault_host_port(self):
        for tls,port,expected in ((True,443,b'Host: school.example.edu.cn\r\n'),
                                  (False,80,b'Host: school.example.edu.cn\r\n'),
                                  (True,8443,b'Host: school.example.edu.cn:8443\r\n'),
                                  (False,8088,b'Host: school.example.edu.cn:8088\r\n')):
            with self.subTest(tls=tls,port=port):
                connection=_PinnedConnection('school.example.edu.cn',port,'93.184.216.34',3,tls)
                connection.sock=Mock()
                connection.request('GET','/admissions')
                wire=b''.join(call.args[0] for call in connection.sock.sendall.call_args_list)
                self.assertIn(expected,wire)

    def test_pinned_connection_still_checks_the_original_hostname_using_verified_tls(self):
        context=Mock();network_socket=Mock()
        with patch('collectors.discovery.socket.create_connection',return_value=network_socket) as connect, \
                patch('collectors.discovery.ssl.create_default_context',return_value=context):
            connection=_PinnedConnection('school.example.edu.cn',443,'93.184.216.34',3,True)
            connection.connect()
        connect.assert_called_once_with(('93.184.216.34',443),3)
        context.wrap_socket.assert_called_once_with(network_socket,server_hostname='school.example.edu.cn')


if __name__=='__main__':unittest.main()
