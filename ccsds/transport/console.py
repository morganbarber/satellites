"""
Persistent serial and TCP console session interface for interactive bootloaders (e.g. U-Boot).
"""

import re
import socket
import time
from typing import Optional, Pattern, Union

from ccsds.exceptions import TransmissionError

try:
    import serial
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False
    serial = None


class PersistentConsole:
    """
    Persistent interactive console interface supporting TCP sockets and Serial connections.
    Designed for embedded bootloader interaction (e.g. U-Boot prompts).
    """

    def __init__(self, target_host: str = "127.0.0.1", target_port: int = 23,
                 serial_port: Optional[str] = None, baudrate: int = 115200,
                 connection_type: str = "tcp", default_timeout: float = 3.0):
        self.target_host = target_host
        self.target_port = target_port
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.connection_type = connection_type.lower()
        self.default_timeout = default_timeout

        self.socket: Optional[socket.socket] = None
        self.serial_conn: Optional[object] = None
        self.is_connected: bool = False

    def connect(self) -> None:
        """Establishes persistent connection over TCP socket or Serial interface."""
        if self.is_connected:
            return

        if self.connection_type == "tcp":
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(self.default_timeout)
                self.socket.connect((self.target_host, self.target_port))
                self.is_connected = True
            except (socket.error, OSError) as exc:
                self.close()
                raise TransmissionError(f"Failed to connect to TCP target {self.target_host}:{self.target_port}: {exc}") from exc

        elif self.connection_type == "serial":
            if not HAS_PYSERIAL:
                raise TransmissionError("PySerial module is required for serial connection mode.")
            if not self.serial_port:
                raise TransmissionError("serial_port must be specified for serial connection mode.")
            try:
                self.serial_conn = serial.Serial(
                    port=self.serial_port,
                    baudrate=self.baudrate,
                    timeout=self.default_timeout
                )
                self.is_connected = True
            except Exception as exc:
                self.close()
                raise TransmissionError(f"Failed to open serial port {self.serial_port}: {exc}") from exc
        else:
            raise TransmissionError(f"Unsupported connection type: {self.connection_type}")

    def close(self) -> None:
        """Closes active console connection."""
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None

        self.is_connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def send_raw(self, data: bytes) -> None:
        """Sends raw bytes to the connected stream."""
        if not self.is_connected:
            self.connect()

        try:
            if self.connection_type == "tcp" and self.socket:
                self.socket.sendall(data)
            elif self.connection_type == "serial" and self.serial_conn:
                self.serial_conn.write(data)
                self.serial_conn.flush()
        except Exception as exc:
            raise TransmissionError(f"Error transmitting data on console: {exc}") from exc

    def read_raw(self, max_bytes: int = 4096, timeout: Optional[float] = None) -> bytes:
        """Reads raw bytes from stream up to max_bytes."""
        if not self.is_connected:
            self.connect()

        effective_timeout = timeout if timeout is not None else self.default_timeout

        try:
            if self.connection_type == "tcp" and self.socket:
                self.socket.settimeout(effective_timeout)
                return self.socket.recv(max_bytes)
            elif self.connection_type == "serial" and self.serial_conn:
                old_timeout = self.serial_conn.timeout
                self.serial_conn.timeout = effective_timeout
                data = self.serial_conn.read(max_bytes)
                self.serial_conn.timeout = old_timeout
                return data
            return b""
        except socket.timeout:
            return b""
        except Exception as exc:
            raise TransmissionError(f"Error reading raw data from console: {exc}") from exc

    def flush_input(self) -> bytes:
        """Flushes and returns all unread input buffer content non-blockingly."""
        flushed = b""
        while True:
            chunk = self.read_raw(max_bytes=1024, timeout=0.05)
            if not chunk:
                break
            flushed += chunk
        return flushed

    def read_until(self, prompt: Union[str, Pattern] = "=>", timeout: Optional[float] = None, regex: bool = False) -> str:
        """
        Reads incoming console text until prompt pattern is found or timeout occurs.

        :param prompt: String or compiled regex pattern to match as prompt.
        :param timeout: Maximum seconds to wait for prompt.
        :param regex: Treat prompt as a regex pattern string if True.
        :return: Accumulated decoded output text.
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout
        start_time = time.time()
        accumulated = ""

        if regex and isinstance(prompt, str):
            prompt_pattern = re.compile(prompt)
        else:
            prompt_pattern = prompt

        while (time.time() - start_time) < effective_timeout:
            chunk = self.read_raw(max_bytes=512, timeout=0.1)
            if chunk:
                accumulated += chunk.decode("utf-8", errors="ignore")

                if isinstance(prompt_pattern, re.Pattern):
                    if prompt_pattern.search(accumulated):
                        return accumulated
                elif isinstance(prompt_pattern, str):
                    if prompt_pattern in accumulated:
                        return accumulated

            time.sleep(0.01)

        return accumulated

    def drain_boot_banner(self, prompt: str = "=>", timeout: Optional[float] = None) -> str:
        """
        Drains the full boot banner by reading until no new data arrives for a
        stabilization period. Waits for the final prompt after boot completes.

        :param prompt: The shell prompt to look for.
        :param timeout: Maximum time to wait for the banner to complete.
        :return: The full boot banner text.
        """
        effective_timeout = timeout if timeout is not None else max(self.default_timeout, 10.0)
        start_time = time.time()
        accumulated = ""
        stable_duration = 1.5  # seconds of silence = banner done
        last_data_time = time.time()

        while (time.time() - start_time) < effective_timeout:
            chunk = self.read_raw(max_bytes=4096, timeout=0.2)
            if chunk:
                accumulated += chunk.decode("utf-8", errors="ignore")
                last_data_time = time.time()
                # If we've seen the prompt and data has stopped, we're done
                if prompt in accumulated and (time.time() - last_data_time) > 0.3:
                    break
            else:
                # No data — check if we've been stable long enough
                if accumulated and (time.time() - last_data_time) > stable_duration:
                    break
            time.sleep(0.05)

        return accumulated

    def send_command(self, command: str, prompt: str = "=>", timeout: Optional[float] = None,
                     strip_echo: bool = True, newline: str = "\n") -> str:
        """
        Sends a command string to the console and reads response until target prompt appears.

        :param command: Command string to transmit.
        :param prompt: Target prompt string to wait for (e.g., "=>").
        :param timeout: Command response timeout.
        :param strip_echo: If True, strips command line echo and prompt from returned output.
        :param newline: Line terminator sequence (default "\n").
        :return: Cleaned string output returned by bootloader console.
        """
        # Drain any pending boot banner / prior output before sending command
        banner = self.drain_boot_banner(prompt=prompt, timeout=timeout)
        if banner:
            # Silently consumed boot banner
            pass

        payload = f"{command}{newline}".encode("utf-8")
        self.send_raw(payload)

        # Use a generous timeout for the command response
        cmd_timeout = timeout if timeout is not None else max(self.default_timeout, 10.0)
        raw_output = self.read_until(prompt=prompt, timeout=cmd_timeout)

        if not strip_echo:
            return raw_output

        cleaned = raw_output
        # Strip command echo
        if command and command in cleaned:
            cleaned = cleaned.split(command, 1)[-1]
            if cleaned.startswith("\r\n"):
                cleaned = cleaned[2:]
            elif cleaned.startswith("\n"):
                cleaned = cleaned[1:]

        # Strip ending prompt
        cleaned = cleaned.rstrip()
        if prompt and cleaned.endswith(prompt):
            cleaned = cleaned[:-len(prompt)]

        return cleaned.strip()

    def interrupt_boot(self, keys: str = " ", prompt: str = "=>", max_attempts: int = 15,
                       interval: float = 0.1, timeout: float = 5.0) -> bool:
        """
        Sends break key sequences (e.g., space or Ctrl+C) to halt bootloader countdown
        and drop into the interactive prompt.

        :param keys: String containing key sequence to transmit (e.g. " " or "\x03" for Ctrl+C).
        :param prompt: Target prompt expected after boot interruption.
        :param max_attempts: Number of key stroke transmission retries.
        :param interval: Delay between key stroke attempts.
        :param timeout: Timeout to wait for prompt after interruption attempt.
        :return: True if prompt was successfully reached, False otherwise.
        """
        if not self.is_connected:
            self.connect()

        self.flush_input()
        key_bytes = keys.encode("utf-8")

        for _ in range(max_attempts):
            self.send_raw(key_bytes)
            time.sleep(interval)
            buffer_content = self.flush_input().decode("utf-8", errors="ignore")
            if prompt in buffer_content:
                return True

        output = self.read_until(prompt=prompt, timeout=timeout)
        return prompt in output
