# satelites-ccsds

A Python library and CLI tool for processing CCSDS Telecommand (TC) and Telemetry (TM) space communication protocols (CCSDS 133.0-B-2, 232.0-B-3, 132.0-B-3).

## Installation

clone the repo

## Usage

### Python API

```python
from ccsds import SpacePacket, TCTransferFrame, StatefulSession

# Construct Space Packet and TC Frame
packet = SpacePacket(apid=42, payload=b"HEALTHCHECK")
frame = TCTransferFrame(scid=12, vcid=3, payload=packet.pack())
raw_bytes = frame.pack()

# Execute automated sequence over UDP/TCP
with StatefulSession("127.0.0.1", 9000, protocol="udp", scid=12, vcid=3, apid=42) as session:
    session.run_sequence(sync_payload=b"BEGIN", next_payload=b"GETFLAG")
```

### CLI Diagnostics

```bash
# Dry-run frame inspection
ccsds --dry-run "GIVE-ME-THE-FLAG" --scid 12 --apid 42 --vcid 3

# Send frame over UDP/TCP
ccsds "HEALTHCHECK" --target 127.0.0.1 --port 9000 --scid 12 --apid 42 --vcid 3

# Automated sequence execution
ccsds --auto-sequence --target 127.0.0.1 --port 9000 --proto udp --scid 12 --apid 42 --vcid 3
```
