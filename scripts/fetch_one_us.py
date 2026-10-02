# -*- coding: utf-8 -*-
# 用 HTTP Range 从 14.6GB 的 US-365K images.zip 里只取一张图。
# 思路：zip 中央目录在尾部 -> Range 取尾部 -> 解析 EOCD -> Range 取中央目录
#      -> 找到目标成员 -> Range 取局部头与数据 -> 解压落盘。
# 总传输量只有几 MB，不需要下载整包。
import os
import struct
import urllib.request
import zlib

REPO = "JJY-0823/US-365K"
URL = "https://huggingface.co/datasets/%s/resolve/main/images.zip" % REPO
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us365k_sample.jpg")
WANT = "236482_040210_33b84a56-1cbf-4667-9b07-0be08fc4c85e.jpg"


def rng(start, end):
    req = urllib.request.Request(URL, headers={"Range": "bytes=%d-%d" % (start, end)})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


req = urllib.request.Request(URL, method="HEAD")
with urllib.request.urlopen(req, timeout=60) as r:
    size = int(r.headers["Content-Length"])
print("zip size =", size)

tail_len = min(4 * 1024 * 1024, size)
tail = rng(size - tail_len, size - 1)
print("tail fetched", len(tail))

i = tail.rfind(b"PK\x05\x06")
if i < 0:
    raise SystemExit("EOCD not found")
eocd = tail[i:i + 22]
vals = struct.unpack("<IHHHHIIH", eocd)
n_entries = vals[4]
cd_size = vals[5]
cd_off = vals[6]
print("EOCD: entries =", n_entries, "cd_size =", cd_size, "cd_off =", cd_off)

# ZIP64：0xFFFFFFFF / 0xFFFF 是哨兵，真实值在 ZIP64 EOCD 记录里
if cd_off == 0xFFFFFFFF or n_entries == 0xFFFF or cd_size == 0xFFFFFFFF:
    loc = tail.rfind(b"PK\x06\x07")
    if loc < 0:
        raise SystemExit("ZIP64 EOCD locator not found")
    z64_off = struct.unpack("<Q", tail[loc + 8:loc + 16])[0]
    print("zip64 eocd at", z64_off)
    rec = rng(z64_off, z64_off + 55)  # PK\x06\x06 + 固定长度
    if rec[:4] != b"PK\x06\x06":
        raise SystemExit("bad zip64 eocd signature")
    n_entries = struct.unpack("<Q", rec[32:40])[0]
    cd_size = struct.unpack("<Q", rec[40:48])[0]
    cd_off = struct.unpack("<Q", rec[48:56])[0]
    print("ZIP64 -> entries =", n_entries, "cd_size =", cd_size, "cd_off =", cd_off)

cd = rng(cd_off, cd_off + cd_size - 1)
print("cd fetched", len(cd))

p = 0
found = None
first_jpg = None
while p < len(cd) - 4 and cd[p:p + 4] == b"PK\x01\x02":
    hdr = struct.unpack("<IHHHHHHIIIHHHHHII", cd[p:p + 46])
    csize, usize, nlen, elen, clen, lho = hdr[8], hdr[9], hdr[10], hdr[11], hdr[12], hdr[16]
    name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")

    # ZIP64 扩展字段(0x0001)：按固定顺序存放那些被写成 0xFFFFFFFF 的值
    if usize == 0xFFFFFFFF or csize == 0xFFFFFFFF or lho == 0xFFFFFFFF:
        ex = cd[p + 46 + nlen:p + 46 + nlen + elen]
        q = 0
        while q < len(ex) - 4:
            xid, xsz = struct.unpack("<HH", ex[q:q + 4])
            body = ex[q + 4:q + 4 + xsz]
            if xid == 0x0001:
                b = 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", body[b:b + 8])[0]
                    b += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", body[b:b + 8])[0]
                    b += 8
                if lho == 0xFFFFFFFF:
                    lho = struct.unpack("<Q", body[b:b + 8])[0]
                    b += 8
                break
            q += 4 + xsz

    if name.lower().endswith((".jpg", ".jpeg", ".png")):
        if first_jpg is None:
            first_jpg = (name, csize, usize, lho)
        if os.path.basename(name) == WANT:
            found = (name, csize, usize, lho)
            break
    p += 46 + nlen + elen + clen

target = found or first_jpg
if not target:
    raise SystemExit("no image member found")

name, csize, usize, lho = target
print("picked:", name, "csize =", csize, "usize =", usize, "lho =", lho)

head = rng(lho, lho + 29)
method = head[8]
nlen2, elen2 = struct.unpack("<HH", head[26:30])
data_start = lho + 30 + nlen2 + elen2
blob = rng(data_start, data_start + csize - 1)
print("blob", len(blob), "method", method)

if method == 0:
    raw = blob
elif method == 8:
    raw = zlib.decompressobj(-zlib.MAX_WBITS).decompress(blob)
else:
    raise SystemExit("unsupported method %d" % method)

with open(OUT, "wb") as f:
    f.write(raw)
print("SAVED", OUT, len(raw), "bytes; JPEG SOI ok:", raw[:2] == b"\xff\xd8")
