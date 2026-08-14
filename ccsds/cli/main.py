"""
Command-line interface entry point for CCSDS Telecommand Diagnostic Client.
"""

import argparse
import binascii
import sys
import unittest

from ccsds.cli.formatter import print_frame_inspection
from ccsds.exceptions import TransmissionError, ValidationError
from ccsds.models.space_packet import SpacePacket
from ccsds.models.tc_frame import TCTransferFrame
from ccsds.transport.client import send_payload
from ccsds.transport.console import PersistentConsole
from ccsds.transport.session import StatefulSession


def parse_arguments(args=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="CCSDS Telecommand Diagnostic Client & Frame Builder"
    )

    parser.add_argument("-t", "--target", help="Target IP or hostname")
    parser.add_argument("-p", "--port", type=int, help="Target port")
    parser.add_argument("--proto", choices=["tcp", "udp", "zmq"], default="udp", help="Transport protocol (default: udp)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Socket timeout in seconds (default: 3.0)")
    parser.add_argument("--recv", action="store_true", help="Listen for UDP/ZMQ response after transmission")
    parser.add_argument("--afsk", action="store_true", help="Enable AFSK1200 modulation for physical radio link")

    # Spacecraft & Channel IDs
    parser.add_argument("--scid", type=int, default=0, help="Spacecraft ID (10-bit: 0-1023), default: 0")
    parser.add_argument("--vcid", type=int, default=0, help="Virtual Channel ID (6-bit: 0-63), default: 0")
    parser.add_argument("--apid", type=int, default=1, help="Application Process ID (11-bit: 0-2047), default: 1")
    parser.add_argument("--packet-type", type=int, choices=[0, 1], default=1, help="Space Packet Type: 0=Telemetry, 1=Telecommand (default)")

    # CCSDS Header Flags
    parser.add_argument(
        "--bypass", type=int, choices=[0, 1], default=1,
        help="COP-1 Bypass Flag: 1=Expedited (default), 0=Sequence-Controlled"
    )
    parser.add_argument("--seq-num", type=int, default=0, help="TC Frame Sequence Number N(S) (0-255), default: 0")
    parser.add_argument(
        "--seq-flags", type=int, choices=[0, 1, 2, 3], default=3,
        help="Space Packet Sequence Flags (0=Cont, 1=First, 2=Last, 3=Unsegmented)"
    )
    parser.add_argument(
        "--sec-header", type=int, choices=[0, 1], default=0,
        help="Secondary Header Flag (0=None, 1=Present)"
    )

    # Payload options
    parser.add_argument("--hex", action="store_true", help="Parse payload string as hex instead of raw ASCII")
    parser.add_argument("-f", "--file", help="Path to file containing payload data")
    parser.add_argument("payload", nargs="?", help="The payload string (ASCII or Hex)")

    # Execution modes
    parser.add_argument("--dry-run", action="store_true", help="Inspect packet structure without transmitting")
    parser.add_argument("--test", action="store_true", help="Run internal unit test suite")
    parser.add_argument("--sp-only", action="store_true", help="Transmit only the Space Packet, skipping the TC Transfer Frame wrapper")

    # Stateful Sequence Automation Options
    parser.add_argument(
        "--auto-sequence", action="store_true",
        help="Run automated stateful sequence (sync -> counter ACK -> next command)"
    )
    parser.add_argument("--start-counter", type=int, default=0, help="Initial sequence counter (0-255), default: 0")
    parser.add_argument("--sync-payload", default="BEGIN", help="Initial sync payload string (default: BEGIN)")
    parser.add_argument(
        "--next-payload", default="GETFLAG",
        help="Follow-up payload string after ACK (default: GETFLAG)"
    )
    parser.add_argument(
        "--payload-format", choices=["auto", "binary_colon", "binary_raw", "hex_text", "dec_text"], default="auto",
        help="Payload counter formatting mode (default: auto)"
    )

    # Console / U-Boot Interaction Options
    parser.add_argument("--console", action="store_true", help="Run interactive/command persistent console session")
    parser.add_argument("--console-type", choices=["tcp", "serial"], default="tcp", help="Console connection type (default: tcp)")
    parser.add_argument("--console-cmd", help="Command string to send to interactive console")
    parser.add_argument("--serial-port", help="Serial port device path (e.g. /dev/ttyUSB0)")
    parser.add_argument("--baudrate", type=int, default=115200, help="Serial baudrate (default: 115200)")
    parser.add_argument("--prompt", default="=>", help="Target prompt string for console (default: =>)")
    parser.add_argument("--interrupt-boot", action="store_true", help="Interrupt boot countdown before console command execution")

    return parser.parse_args(args)


def run_tests():
    """Runs test suite when invoked via --test flag."""
    print("[*] Running CCSDS Engine Unit Tests...")
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def execute_console(args):
    """Executes persistent console command / boot interruption routine."""
    if args.console_type == "tcp" and (not args.target or not args.port):
        print("[-] Error: --target and --port are required for TCP console connection.")
        sys.exit(1)
    if args.console_type == "serial" and not args.serial_port:
        print("[-] Error: --serial-port is required for Serial console connection.")
        sys.exit(1)

    print(f"[*] Connecting to {args.console_type.upper()} console...")
    try:
        console = PersistentConsole(
            target_host=args.target or "127.0.0.1",
            target_port=args.port or 23,
            serial_port=args.serial_port,
            baudrate=args.baudrate,
            connection_type=args.console_type,
            default_timeout=args.timeout,
            afsk=args.afsk
        )
        with console:
            if args.interrupt_boot:
                print(f"[*] Interrupting boot countdown (waiting for prompt '{args.prompt}')...")
                interrupted = console.interrupt_boot(prompt=args.prompt, timeout=args.timeout)
                if interrupted:
                    print(f"[+] Boot interrupted! Target prompt '{args.prompt}' detected.")
                else:
                    print(f"[!] Prompt '{args.prompt}' not detected during boot interrupt attempt.")

            if args.console_cmd:
                print(f"[*] Sending command to console: {args.console_cmd}")
                output = console.send_command(args.console_cmd, prompt=args.prompt, timeout=args.timeout)
                print(f"[+] Console Command Output:\n{output}")
            elif not args.interrupt_boot:
                flushed = console.flush_input()
                print(f"[+] Console session established. Initial output:\n{flushed.decode('utf-8', errors='ignore')}")

        sys.exit(0)
    except TransmissionError as e:
        print(f"[-] Console operation failed: {e}")
        sys.exit(1)



def execute_auto_sequence(args):
    """Executes stateful automated telecommand sequence."""
    if not args.target or not args.port:
        print("[-] Error: --target and --port are required for --auto-sequence.")
        sys.exit(1)

    sync_bytes = binascii.unhexlify(args.sync_payload) if args.hex else args.sync_payload.encode('utf-8')
    next_bytes = binascii.unhexlify(args.next_payload) if args.hex else args.next_payload.encode('utf-8')

    print(f"[*] Starting Stateful Sequence Automation over {args.proto.upper()} to {args.target}:{args.port}...")
    print(
        f"[*] Config: SCID={args.scid}, VCID={args.vcid}, APID={args.apid}, "
        f"Bypass={args.bypass}, PayloadFormat={args.payload_format}"
    )

    with StatefulSession(
        target_host=args.target,
        target_port=args.port,
        protocol=args.proto,
        scid=args.scid,
        vcid=args.vcid,
        apid=args.apid,
        bypass=args.bypass,
        seq_num=args.seq_num,
        timeout=args.timeout,
        afsk=args.afsk
    ) as session:
        try:
            session.run_sequence(
                start_counter=args.start_counter,
                sync_payload=sync_bytes,
                next_payload=next_bytes,
                fmt_style=args.payload_format
            )
            print("\n[+] Sequence Automation Completed Successfully!")
            sys.exit(0)
        except TransmissionError as e:
            print(f"[-] Sequence execution failed: {e}")
            sys.exit(1)


def extract_user_payload(args) -> bytes:
    """Extracts raw bytes from file input or positional argument."""
    if args.file:
        try:
            with open(args.file, "rb") as f:
                return f.read()
        except OSError as e:
            print(f"[-] Error reading file '{args.file}': {e}")
            sys.exit(1)
    elif args.payload:
        try:
            if args.hex:
                return binascii.unhexlify(args.payload.replace(" ", ""))
            return args.payload.encode('utf-8')
        except binascii.Error as e:
            print(f"[-] Error: Payload is not valid hexadecimal: {e}")
            sys.exit(1)
    else:
        print("[-] Error: A payload string or --file input is required (unless running --test, --auto-sequence, or --console).")
        sys.exit(1)


def main(cli_args=None):
    """Main CLI execution routine."""
    args = parse_arguments(cli_args)

    if args.test:
        run_tests()

    if args.console:
        execute_console(args)

    if args.auto_sequence:
        execute_auto_sequence(args)

    user_data = extract_user_payload(args)

    # Build Space Packet & TC Transfer Frame
    try:
        sp = SpacePacket(
            apid=args.apid,
            payload=user_data,
            packet_type=args.packet_type,
            sec_header_flag=args.sec_header,
            seq_flags=args.seq_flags
        )
        sp_bytes = sp.pack()

        tc = TCTransferFrame(
            scid=args.scid,
            vcid=args.vcid,
            payload=sp_bytes,
            bypass=args.bypass,
            seq_num=args.seq_num
        )
        tc_bytes = tc.pack()
    except (ValueError, ValidationError) as e:
        print(f"[-] Encoding error: {e}")
        sys.exit(1)

    # Display inspection report
    if args.sp_only:
        print_frame_inspection(sp, None, sp_bytes)
    else:
        print_frame_inspection(sp, tc, tc_bytes)

    if args.dry_run:
        print("[*] Dry-run mode enabled. Skipping transmission.")
        return

    # Validate target network settings
    if not args.target or not args.port:
        print("[-] Error: --target and --port are required for transmission (or use --dry-run).")
        sys.exit(1)

    print(f"[*] Transmitting over {args.proto.upper()} to {args.target}:{args.port}...")
    try:
        data_to_send = sp_bytes if args.sp_only else tc_bytes
        send_payload(
            target_host=args.target,
            target_port=args.port,
            protocol=args.proto,
            data=data_to_send,
            timeout=args.timeout,
            listen_response=args.recv,
            afsk=args.afsk
        )
    except TransmissionError:
        sys.exit(1)



if __name__ == "__main__":
    main()
