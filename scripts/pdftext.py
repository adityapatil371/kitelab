"""Minimal PDF text extractor: zlib-inflate every stream, pull Tj/TJ strings."""
import re, sys, zlib

raw = open(sys.argv[1], "rb").read()
print(f"file: {len(raw):,} bytes", file=sys.stderr)

streams = []
for m in re.finditer(rb"stream\r?\n", raw):
    start = m.end()
    end = raw.find(b"endstream", start)
    if end < 0:
        continue
    blob = raw[start:end]
    try:
        streams.append(zlib.decompress(blob))
    except zlib.error:
        try:
            streams.append(zlib.decompressobj().decompress(blob))
        except zlib.error:
            pass
print(f"streams inflated: {len(streams)}", file=sys.stderr)

def unescape(b):
    out, i = bytearray(), 0
    while i < len(b):
        c = b[i]
        if c == 0x5C and i + 1 < len(b):
            nxt = b[i+1]
            mapping = {0x6E:10, 0x72:13, 0x74:9, 0x62:8, 0x66:12,
                       0x28:0x28, 0x29:0x29, 0x5C:0x5C}
            if nxt in mapping:
                out.append(mapping[nxt]); i += 2; continue
            if 0x30 <= nxt <= 0x37:
                oct_digits = b[i+1:i+4]
                k = 0
                while k < 3 and k < len(oct_digits) and 0x30 <= oct_digits[k] <= 0x37:
                    k += 1
                out.append(int(oct_digits[:k], 8) & 0xFF); i += 1 + k; continue
            i += 2; continue
        out.append(c); i += 1
    return bytes(out)

TEXT_OPS = re.compile(rb"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>|TJ|Tj|T\*|Td|TD|'|\"")
pages = []
for s in streams:
    if b"Tj" not in s and b"TJ" not in s:
        continue
    buf = []
    for tok in TEXT_OPS.finditer(s):
        t = tok.group()
        if t.startswith(b"("):
            buf.append(unescape(t[1:-1]).decode("latin-1"))
        elif t.startswith(b"<"):
            hx = re.sub(rb"\s", b"", t[1:-1])
            if len(hx) % 2 == 0:
                try:
                    d = bytes.fromhex(hx.decode())
                    buf.append(d.decode("utf-16-be", "replace")
                               if len(d) % 2 == 0 and d[:1] == b"\x00"
                               else d.decode("latin-1"))
                except ValueError:
                    pass
        elif t in (b"T*", b"Td", b"TD", b"'", b'"'):
            buf.append("\n")
    txt = "".join(buf)
    if txt.strip():
        pages.append(txt)

print(f"content streams with text: {len(pages)}", file=sys.stderr)
out = "\n\n=== PAGE BREAK ===\n\n".join(pages)
open(sys.argv[2], "w").write(out)
print(f"chars written: {len(out):,}", file=sys.stderr)
