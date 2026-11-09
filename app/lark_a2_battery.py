"""Battery GET for HOLLYLAND LARK A2 (猛玛).

Wire facts come from the official WebHID page (res.hollyland.com/6118):
  report id 0x55, frame PACKET_SIZE 63
  request : AA DD | cmd | device | len_hi len_lo | payload
  response: BB DD | cmd | device | len_hi len_lo | payload
  HEART_GET = 0x10, RX device byte = 0x40
  heart payload: [0]=tx1 online [1]=tx2 online [2]=tx1 level [3]=tx2 level
                 [6]=noise level
                 [15]=bit0 tx1 muted, bit1 tx2 muted, bit2 tx1 charging, bit3 tx2 charging
Only the read path is implemented; nothing here writes device settings.
"""

VID = 0x3547
PID = 0x0411
REPORT_ID = 0x55
PACKET_SIZE = 63
HEART_GET = 0x10
DEVICE_RX = 0x40
DEVICE_REPLY_RX = 0x80
FRAME_HEAD = 0xAA, 0xDD
REPLY_HEAD = 0xBB, 0xDD


def matches(iface):
    """The 0xFF90 vendor page that carries the 6118 frame format."""
    return (iface.vid == VID and iface.pid == PID
            and iface.usage_page == 0xFF90
            and iface.input_len == 64 and iface.output_len == 64)


def request():
    """64 bytes including the leading report id, ready for write_output."""
    frame = bytearray(PACKET_SIZE)
    frame[0] = FRAME_HEAD[0]
    frame[1] = FRAME_HEAD[1]
    frame[2] = HEART_GET
    frame[3] = DEVICE_RX
    return bytes([REPORT_ID]) + bytes(frame)


def decode(raw):
    """Return (level, status) for the lowest online transmitter, or None.

    Only the receiver heartbeat (device byte 0x80) is parsed: transmitter frames
    reuse the command byte but their payload layout is not verified here, so a
    0x81/0x82 frame is rejected instead of being read as battery data.
    """
    r = bytes(raw or b'')
    off = 1 if len(r) > 6 and r[0] == REPORT_ID else 0
    if len(r) < off + 6 + 16:
        return None
    if ((r[off], r[off + 1]) != REPLY_HEAD
            or r[off + 2] != HEART_GET or r[off + 3] != DEVICE_REPLY_RX):
        return None
    length = (r[off + 4] << 8) | r[off + 5]
    payload = r[off + 6:off + 6 + length]
    if len(payload) < 16:
        return None
    flags = payload[15]
    # Keep each transmitter's own charging bit: the lowest level must not
    # inherit the other transmitter's charging state.
    live = []
    if payload[0] == 1 and payload[2] <= 100:
        live.append((payload[2], flags & 0x04))
    if payload[1] == 1 and payload[3] <= 100:
        live.append((payload[3], flags & 0x08))
    if not live:
        return None
    level, charging = min(live, key=lambda item: item[0])
    return level, 'charging' if charging else 'discharging'
