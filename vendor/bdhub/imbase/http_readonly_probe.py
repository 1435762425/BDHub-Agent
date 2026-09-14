"""IM Protobuf只读探查校验器；只允许已确认的203初始化读取，拒绝100发送命令。"""
READ_HOST = "oec-im-tt-sg.tiktokglobalshopv.com"
READ_PATH = "/v2/message/get_by_user_init"


def wire_fields(data: bytes) -> dict[int, list[object]]:
    if not isinstance(data, bytes) or len(data) > 8_000_000:
        raise ValueError("im_probe_payload_size")
    pos = 0
    fields = {}
    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data): raise ValueError("im_probe_truncated")
            byte = data[pos]; pos += 1
            value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError("im_probe_varint")
    while pos < len(data):
        tag = varint(); field, kind = tag >> 3, tag & 7
        if field == 0: raise ValueError("im_probe_field")
        if kind == 0: value = varint()
        elif kind in (1, 2, 5):
            size = varint() if kind == 2 else 8 if kind == 1 else 4
            if pos + size > len(data): raise ValueError("im_probe_truncated")
            value = data[pos:pos+size]; pos += size
        else: raise ValueError("im_probe_wire_type")
        fields.setdefault(field, []).append(value)
    return fields


def validate_init_read(data: bytes) -> int:
    fields = wire_fields(data)
    if fields.get(1) != [203] or len(fields.get(2, [])) != 1:
        raise ValueError("im_probe_non_read_command")
    bodies = fields.get(8, [])
    if len(bodies) != 1 or set(wire_fields(bodies[0])) != {203}:
        raise ValueError("im_probe_non_read_body")
    return int(fields[2][0])


def response_summary(data: bytes, *, sequence: int) -> dict:
    fields = wire_fields(data)
    status = fields.get(3, [0])
    if fields.get(1) != [203] or fields.get(2) != [sequence] or len(status) != 1:
        raise ValueError("im_probe_response_mismatch")
    return {"cmd": 203, "status_code": int(status[0]), "sequence_matches": True,
            "response_bytes": len(data), "body_present": bool(fields.get(6))}
