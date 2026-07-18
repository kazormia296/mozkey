import argparse
import json
import struct
import sys
import time
import urllib.error
import urllib.request

import win32con
import win32file
import win32pipe
import win32security
import pywintypes


PIPE_NAME_DEFAULT = r"\\.\pipe\mozc_zenz_scorer"
LLAMA_URL_DEFAULT = "http://127.0.0.1:18080/completion"

MAGIC = 0x315A4E5A  # "ZNZ1"
VERSION = 2
KIND_REQUEST = 1
KIND_RESPONSE = 2

STATUS_OK = 0
STATUS_ERROR = 1
STATUS_TIMEOUT = 2

REQ_HDR = struct.Struct("<IHHIIIII")
RESP_HDR = struct.Struct("<IHHIIIII")


def make_pipe_security_attributes():
    """Creates permissive security attributes for local test.

    DACL:
      Everyone: Generic All

    SACL mandatory label:
      Low integrity object

    The low integrity label is intentionally permissive for local experiments.
    For production, replace this with a current-user-only DACL and avoid
    elevated/non-elevated process mismatch.
    """
    sddl = "D:(A;;GA;;;WD)S:(ML;;NW;;;LW)"

    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl,
        win32security.SDDL_REVISION_1,
    )

    sa = pywintypes.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    sa.bInheritHandle = False
    return sa


def read_exact(handle, size: int) -> bytes:
    chunks = []
    remaining = size

    while remaining > 0:
        err, data = win32file.ReadFile(handle, remaining)
        if err != 0:
            raise RuntimeError(f"ReadFile failed: {err}")
        if not data:
            raise EOFError("pipe closed")
        chunks.append(data)
        remaining -= len(data)

    return b"".join(chunks)


def write_all(handle, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        err, written = win32file.WriteFile(handle, data[offset:])
        if err != 0:
            raise RuntimeError(f"WriteFile failed: {err}")
        if written == 0:
            raise RuntimeError("WriteFile wrote 0 bytes")
        offset += written


def clean_zenz_output(text: str, max_chars: int) -> str:
    if not text:
        return ""

    # Stop at prompt private-use markers or typical special strings.
    cut_markers = [
        "\uee00", "\uee01", "\uee02", "\uee03", "\uee04", "\uee05", "\uee06",
        "<s>", "</s>", "<unk>", "<|endoftext|>",
        "\r", "\n",
    ]

    end = len(text)
    for marker in cut_markers:
        idx = text.find(marker)
        if idx >= 0:
            end = min(end, idx)

    text = text[:end].strip()

    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]

    return text


def call_llama_server(
    url: str,
    prompt: str,
    timeout_msec: int,
    max_output_chars: int,
    n_predict: int,
) -> tuple[int, str, str]:
    payload = {
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0.0,
        "top_k": 1,
        "top_p": 1.0,
        "stream": False,
        "cache_prompt": True,
        "stop": [
            "\uee00", "\uee01", "\uee02", "\uee03", "\uee04", "\uee05", "\uee06",
            "\n",
        ],
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=max(timeout_msec / 1000.0, 0.05),
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
    except TimeoutError:
        return STATUS_TIMEOUT, "", "llama_server_timeout"
    except urllib.error.URLError as e:
        return STATUS_ERROR, "", f"llama_server_url_error: {e}"
    except Exception as e:
        return STATUS_ERROR, "", f"llama_server_error: {e}"

    # llama.cpp /completion endpoint normally returns {"content": "..."}.
    text = data.get("content", "")

    # Some OpenAI-compatible variants may return choices.
    if not text and isinstance(data.get("choices"), list) and data["choices"]:
        choice = data["choices"][0]
        if isinstance(choice, dict):
            text = choice.get("text") or choice.get("message", {}).get("content", "")

    text = clean_zenz_output(text, max_output_chars)

    if not text:
        return STATUS_ERROR, "", f"empty_generation: raw={data!r}"

    return STATUS_OK, text, "ok"


def handle_one_connection(handle, args) -> None:
    started = time.perf_counter()

    header_bytes = read_exact(handle, REQ_HDR.size)
    (
        magic,
        version,
        kind,
        generation,
        timeout_msec,
        max_output_chars,
        prompt_size,
        backend_device_size,
    ) = REQ_HDR.unpack(header_bytes)

    if magic != MAGIC or version != VERSION or kind != KIND_REQUEST:
        debug = f"bad_header magic={magic:x} version={version} kind={kind}"
        send_response(handle, generation, STATUS_ERROR, "", debug, started)
        return

    prompt_bytes = read_exact(handle, prompt_size)
    backend_device_bytes = read_exact(handle, backend_device_size)
    prompt = prompt_bytes.decode("utf-8", errors="replace")
    backend_device = backend_device_bytes.decode("ascii", errors="replace")

    effective_n_predict = args.n_predict
    if max_output_chars > 0:
        effective_n_predict = min(effective_n_predict, max(4, max_output_chars))

    status, value, debug = call_llama_server(
        url=args.url,
        prompt=prompt,
        timeout_msec=timeout_msec,
        max_output_chars=max_output_chars,
        n_predict=effective_n_predict,
    )

    if args.verbose:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        print(
            f"[zenz-bridge] gen={generation} status={status} "
            f"latency={elapsed_ms}ms device={backend_device or 'automatic'} "
            f"value={value!r} debug={debug}",
            flush=True,
        )

    send_response(handle, generation, status, value, debug, started)


def send_response(
    handle,
    generation: int,
    status: int,
    value: str,
    debug: str,
    started: float,
) -> None:
    latency_msec = int((time.perf_counter() - started) * 1000)
    value_bytes = value.encode("utf-8")
    debug_bytes = debug.encode("utf-8")

    response_header = RESP_HDR.pack(
        MAGIC,
        VERSION,
        KIND_RESPONSE,
        generation,
        status,
        latency_msec,
        len(value_bytes),
        len(debug_bytes),
    )

    write_all(handle, response_header)
    if value_bytes:
        write_all(handle, value_bytes)
    if debug_bytes:
        write_all(handle, debug_bytes)


def serve_forever(args) -> None:
    print(f"[zenz-bridge] pipe: {args.pipe}", flush=True)
    print(f"[zenz-bridge] llama: {args.url}", flush=True)

    while True:
        pipe = win32pipe.CreateNamedPipe(
            args.pipe,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            65536,
            65536,
            0,
            make_pipe_security_attributes(),
        )

        try:
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as e:
                # ERROR_PIPE_CONNECTED
                if e.winerror != 535:
                    raise

            handle_one_connection(pipe, args)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if args.verbose:
                print(f"[zenz-bridge] connection error: {e}", file=sys.stderr, flush=True)
        finally:
            try:
                win32file.FlushFileBuffers(pipe)
            except Exception:
                pass
            try:
                win32pipe.DisconnectNamedPipe(pipe)
            except Exception:
                pass
            try:
                win32file.CloseHandle(pipe)
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipe", default=PIPE_NAME_DEFAULT)
    parser.add_argument("--url", default=LLAMA_URL_DEFAULT)
    parser.add_argument("--n-predict", type=int, default=16)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    serve_forever(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
