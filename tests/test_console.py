"""
Unit test suite for PersistentConsole using a local TCP socket server thread.
"""

import socket
import threading
import time
import unittest

from ccsds.exceptions import TransmissionError
from ccsds.transport.console import PersistentConsole


class TestPersistentConsole(unittest.TestCase):
    """Test suite for PersistentConsole TCP & serial console interaction logic."""

    def setUp(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.bind(("127.0.0.1", 0))
        self.server_sock.listen(5)
        self.port = self.server_sock.getsockname()[1]
        self.running = True

    def tearDown(self):
        self.running = False
        try:
            self.server_sock.close()
        except OSError:
            pass

    def test_console_connect_and_send_command(self):
        """Tests command transmission and prompt matching over TCP console."""
        def mock_uboot_server():
            try:
                conn, _ = self.server_sock.accept()
                conn.settimeout(2.0)
                # Send banner
                conn.sendall(b"U-Boot 2023.04 (Aug 13 2026 - 12:00:00 +0000)\nHit any key to stop autoboot: 0\n=> ")
                
                # Receive command line
                cmd_data = conn.recv(1024)
                if b"printenv" in cmd_data:
                    response = b"printenv\r\nbaudrate=115200\r\nbootargs=console=ttyS0,115200\r\n=> "
                    conn.sendall(response)
                conn.close()
            except (socket.timeout, OSError):
                pass

        t = threading.Thread(target=mock_uboot_server)
        t.start()

        with PersistentConsole(target_host="127.0.0.1", target_port=self.port, default_timeout=2.0) as console:
            out = console.send_command("printenv", prompt="=>")
            self.assertIn("baudrate=115200", out)
            self.assertIn("bootargs=console=ttyS0,115200", out)
            self.assertNotIn("printenv", out)  # Command echo stripped
            self.assertNotIn("=>", out)        # Prompt stripped

        t.join()

    def test_console_regex_prompt(self):
        """Tests read_until with regex prompt matching."""
        def mock_prompt_server():
            try:
                conn, _ = self.server_sock.accept()
                conn.sendall(b"System booting...\nSOC_BOARD_42# ")
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=mock_prompt_server)
        t.start()

        console = PersistentConsole(target_host="127.0.0.1", target_port=self.port, default_timeout=2.0)
        console.connect()
        text = console.read_until(prompt=r"\w+#\s", regex=True, timeout=2.0)
        self.assertIn("SOC_BOARD_42# ", text)
        console.close()

        t.join()

    def test_interrupt_boot(self):
        """Tests interrupting boot countdown sequence."""
        def mock_autoboot_server():
            try:
                conn, _ = self.server_sock.accept()
                conn.settimeout(2.0)
                conn.sendall(b"Hit any key to stop autoboot: 3")
                time.sleep(0.1)

                key = conn.recv(100)
                if key:
                    conn.sendall(b"\nAutoboot interrupted!\n=> ")
                conn.close()
            except (socket.timeout, OSError):
                pass

        t = threading.Thread(target=mock_autoboot_server)
        t.start()

        with PersistentConsole(target_host="127.0.0.1", target_port=self.port, default_timeout=2.0) as console:
            success = console.interrupt_boot(keys=" ", prompt="=>", max_attempts=5, interval=0.05, timeout=2.0)
            self.assertTrue(success)

        t.join()

    def test_connection_error_handling(self):
        """Tests error handling for unreachable TCP port."""
        console = PersistentConsole(target_host="127.0.0.1", target_port=1, default_timeout=0.5)
        with self.assertRaises(TransmissionError):
            console.connect()

    def test_serial_missing_pyserial_error(self):
        """Tests exception raised when serial connection mode is requested without pyserial configuration."""
        console = PersistentConsole(serial_port="/dev/nonexistent", connection_type="serial")
        # Should raise TransmissionError if pyserial not installed or port invalid
        with self.assertRaises(TransmissionError):
            console.connect()


if __name__ == "__main__":
    unittest.main()
