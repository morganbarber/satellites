# Satelites CCSDS Engine

A production-ready Python library and CLI diagnostic engine for **CCSDS Telecommand (TC)** and **Telemetry (TM)** protocol processing, supporting space ground station communications and satellite interface testing.

## Standards Supported

- **CCSDS 133.0-B-2**: Space Packet Protocol
- **CCSDS 232.0-B-3**: TC Transfer Frame Protocol
- **CCSDS 132.0-B-3**: TM Transfer Frame Protocol

## Package Architecture

```
satelites/
├── ccsds/                      # Core CCSDS library package
│   ├── __init__.py             # Public API exports
│   ├── __main__.py             # Executable module entry point (python -m ccsds)
│   ├── py.typed                 # PEP 561 typing marker
│   ├── crc.py                  # Table-driven CCSDS CRC16 (Poly 0x1021, Init 0xFFFF)
│   ├── exceptions.py           # Custom exception hierarchy
│   ├── models/                 # Dataclasses & binary serialization
│   │   ├── space_packet.py     # SpacePacket
│   │   ├── tc_frame.py         # TCTransferFrame
│   │   └── tm_frame.py         # TMTransferFrame
│   ├── transport/              # Network & session automation
│   │   ├── client.py           # Single-shot socket transmission
│   │   ├── session.py          # StatefulSession for TCP/UDP sequence automation
│   │   └── payload.py          # Format detection and counter payload formatting
│   └── cli/                    # CLI interface & visual inspection report
│       ├── formatter.py        # Frame inspection breakdown output
│       └── main.py             # CLI parser and entry point
├── tests/                      # Unit & integration tests
└── pyproject.toml              # PEP 517/PEP 621 PyPI package build configuration
```

## Quick Start

### Installation

From PyPI:
```bash
pip install satelites-ccsds
```

Local development mode:
```bash
pip install -e .
```

### Python API Usage

```python
from ccsds import SpacePacket, TCTransferFrame, StatefulSession

# Construct a Space Packet
sp = SpacePacket(
    apid=42,
    payload=b"HEALTHCHECK",
    seq_flags=3,
    seq_count=100
)

# Pack into TC Transfer Frame with FECF CRC16
tc = TCTransferFrame(
    scid=12,
    vcid=3,
    payload=sp.pack(),
    bypass=0,
    seq_num=1
)

frame_bytes = tc.pack()

# Run a stateful multi-step sequence over UDP/TCP
with StatefulSession("127.0.0.1", 9000, protocol="udp", scid=12, vcid=3, apid=42) as session:
    result = session.run_sequence(
        start_counter=0,
        sync_payload=b"BEGIN",
        next_payload=b"GETFLAG",
        fmt_style="auto"
    )
```

### CLI Diagnostics

Execute directly using the module executable:
```bash
python3 -m ccsds --dry-run "GIVE-ME-THE-FLAG" --scid 12 --apid 42 --vcid 3
```

Or using the installed console scripts (`ccsds` or `satelites`):
```bash
# Dry-run frame inspection
ccsds --dry-run "GIVE-ME-THE-FLAG" --scid 12 --apid 42 --vcid 3

# Stateful sequence automation
ccsds --auto-sequence --target 127.0.0.1 --port 9000 --proto udp --scid 12 --apid 42 --vcid 3
```

### Testing

Run test suite:
```bash
python3 -m ccsds --test
# or
python3 -m unittest discover -s tests
```
