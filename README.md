# satelites-ccsds

A Python library and CLI tool for processing CCSDS Telecommand (TC) and Telemetry (TM) space communication protocols (CCSDS 133.0-B-2, 232.0-B-3, 132.0-B-3).

## Installation

Clone the repository and install the dependencies:

```bash
git clone https://github.com/yourusername/satelites-ccsds.git
cd satelites-ccsds
pip install -e .
```

## Usage Examples

The `ccsds` library can be used both as a Python API for integrating into ground station software and as a standalone CLI tool for rapid diagnostics and testing.

### Python API

#### 1. Constructing Space Packets and TC Frames

You can manually construct and pack CCSDS telecommands for transmission to a spacecraft:

```python
from ccsds import SpacePacket, TCTransferFrame

# Create a Space Packet with an Application Process ID (APID) of 42
packet = SpacePacket(apid=42, payload=b"HEALTHCHECK")

# Wrap the Space Packet inside a Telecommand (TC) Transfer Frame
# Specify the Spacecraft ID (SCID) and Virtual Channel ID (VCID)
frame = TCTransferFrame(scid=12, vcid=3, payload=packet.pack())

# Serialize the frame to raw bytes (includes CRC16 generation)
raw_bytes = frame.pack()

# The raw_bytes can now be sent via UDP/TCP or RF radio link
print(f"Prepared {len(raw_bytes)} bytes for transmission: {raw_bytes.hex()}")
```

#### 2. Automated Stateful Sequence Execution

For scenarios requiring a handshake or an automated command sequence (e.g., waiting for an ACK before sending the main payload), use `StatefulSession`:

```python
from ccsds import StatefulSession

# Connect to the simulation box or ground station endpoint
with StatefulSession("127.0.0.1", 9000, protocol="udp", scid=12, vcid=3, apid=42) as session:
    # 1. Sends the sync_payload
    # 2. Waits for the corresponding sequence counter ACK in telemetry
    # 3. Transmits the next_payload automatically upon successful ACK
    session.run_sequence(sync_payload=b"BEGIN", next_payload=b"GETFLAG")
```

#### 3. Interacting with U-Boot via Persistent Console

If the spacecraft is in a bootloader state, you can interact with it using `PersistentConsole`:

```python
from ccsds.transport.console import PersistentConsole

# Connect over TCP or Serial
with PersistentConsole(target_host="127.0.0.1", target_port=31028, console_type="tcp") as console:
    # Interrupt the U-Boot autoboot sequence
    console.interrupt_boot(keys=" ", prompt="=>")
    
    # Send U-Boot memory inspection commands to search for keys/flags
    response = console.send_command("md.b 0x8000000 0x100")
    print(response)
```

#### 4. Parsing Telemetry (TM) Frames

```python
from ccsds import TMTransferFrame

# Assume `rx_bytes` is received from the ground station receiver
rx_bytes = b'...' 

# Parse the incoming TM Frame
tm_frame = TMTransferFrame.unpack(rx_bytes)
print(f"Received telemetry from SCID {tm_frame.scid}, VCID {tm_frame.vcid}")
print(f"Master Channel Frame Count: {tm_frame.mc_frame_count}")
print(f"Payload Data: {tm_frame.payload.hex()}")
```

### CLI Diagnostics

The CLI provides a powerful interface for building frames, sending sequences, and dropping into U-Boot consoles.

#### Basic Telecommand Transmission
Send a raw ASCII payload to the target over UDP:
```bash
ccsds "HEALTHCHECK" --target 127.0.0.1 --port 9000 --scid 12 --apid 42 --vcid 3
```

Send a hex-encoded payload:
```bash
ccsds "474956452d4d452d5448452d464c4147" --hex --target 127.0.0.1 --port 9000 --scid 12 --apid 42 --vcid 3
```

#### Dry-run and Inspection
Inspect the generated frame structure without sending any data over the network:
```bash
ccsds --dry-run "GIVE-ME-THE-FLAG" --scid 12 --apid 42 --vcid 3
```

#### Automated Handshake Sequencing
Execute an automatic stateful transmission (Sends "BEGIN", waits for ACK TM frame, then sends "GETFLAG"):
```bash
ccsds --auto-sequence --target 127.0.0.1 --port 9000 --proto udp \
      --scid 12 --apid 42 --vcid 3 \
      --sync-payload "BEGIN" --next-payload "GETFLAG"
```

#### U-Boot Persistent Console
Interrupt the boot sequence and drop into an interactive U-Boot console session via TCP:
```bash
ccsds --console --console-type tcp --target 127.0.0.1 --port 31028 --interrupt-boot
```

Or send a single command and exit:
```bash
ccsds --console --console-type tcp --target 127.0.0.1 --port 31028 \
      --interrupt-boot --console-cmd "md.b 0x8000000 0x100"
```
