import struct
import win32file

PIPE = r"\\.\pipe\mozc_zenz_scorer"

MAGIC = 0x315A4E5A
VERSION = 2
KIND_REQUEST = 1

REQ_HDR = struct.Struct("<IHHIIIII")
RESP_HDR = struct.Struct("<IHHIIIII")

prompt = "\uee02彼の動きは\uee00セイサイヲカイタ\uee01".encode("utf-8")
backend_device = b""

h = win32file.CreateFile(
    PIPE,
    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
    0,
    None,
    win32file.OPEN_EXISTING,
    0,
    None,
)

header = REQ_HDR.pack(
    MAGIC,
    VERSION,
    KIND_REQUEST,
    1,      # generation
    5000,   # timeout_msec
    128,    # max_output_chars
    len(prompt),
    len(backend_device),
)

win32file.WriteFile(h, header + prompt + backend_device)

_, resp_header_bytes = win32file.ReadFile(h, RESP_HDR.size)
magic, version, kind, generation, status, latency, value_size, debug_size = RESP_HDR.unpack(resp_header_bytes)

_, value_bytes = win32file.ReadFile(h, value_size) if value_size else (0, b"")
_, debug_bytes = win32file.ReadFile(h, debug_size) if debug_size else (0, b"")

print("status:", status)
print("latency:", latency)
print("value:", value_bytes.decode("utf-8", errors="replace"))
print("debug:", debug_bytes.decode("utf-8", errors="replace"))
