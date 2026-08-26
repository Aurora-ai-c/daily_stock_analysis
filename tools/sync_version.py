# -*- coding: utf-8 -*-
"""同步发布 tag 到 dsa_client 版本单源(_version.py + version_info.txt)。

用法: python tools/sync_version.py <tag>  # tag 形如 v1.2.3
写 _version.py(单行 __version__),重写 version_info.txt(filevers/prodvers),
再读回两文件校验一致性;不一致则报错退出非零。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "apps/dsa-cloud-client"
VERSION_PY = CLIENT_DIR / "dsa_client" / "_version.py"
VERSION_INFO = CLIENT_DIR / "version_info.txt"

TAG_RE = re.compile(r"^(?:[A-Za-z]+-)?v(\d+)\.(\d+)\.(\d+)$")
FILEVERS_RE = re.compile(r"filevers=\(([\d,]+)\)")
PRODVERS_RE = re.compile(r"prodvers=\(([\d,]+)\)")


def parse_tag(tag: str) -> tuple[int, int, int]:
    m = TAG_RE.match(tag)
    if not m:
        raise SystemExit(f"tag 格式错误,应为 vX.Y.Z,收到: {tag!r}")
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def write_version_py(ver: tuple[int, int, int]) -> None:
    VERSION_PY.write_text(f'__version__ = "{ver[0]}.{ver[1]}.{ver[2]}"\n', encoding="utf-8")


def write_version_info(ver: tuple[int, int, int]) -> None:
    parts = ",".join(str(x) for x in ver)
    vstr = f"{ver[0]}.{ver[1]}.{ver[2]}"
    VERSION_INFO.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({parts},0),
    prodvers=({parts},0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0,0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'DSA'),
        StringStruct('FileDescription', 'DSA Cloud Client'),
        StringStruct('FileVersion', '{vstr}.0'),
        StringStruct('InternalName', 'dsa-cloud-client'),
        StringStruct('OriginalFilename', 'dsa-cloud-client.exe'),
        StringStruct('ProductName', 'DSA Cloud Client'),
        StringStruct('ProductVersion', '{vstr}.0')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])])
""",
        encoding="utf-8",
    )


def verify(ver: tuple[int, int, int]) -> None:
    vstr = f"{ver[0]}.{ver[1]}.{ver[2]}"
    py_content = VERSION_PY.read_text(encoding="utf-8").strip()
    if py_content != f'__version__ = "{vstr}"':
        raise RuntimeError(f"_version.py 内容不一致: {py_content!r}")
    info = VERSION_INFO.read_text(encoding="utf-8")
    filevers = tuple(int(x) for x in FILEVERS_RE.search(info).group(1).split(","))
    prodvers = tuple(int(x) for x in PRODVERS_RE.search(info).group(1).split(","))
    if filevers != (ver[0], ver[1], ver[2], 0) or prodvers != (ver[0], ver[1], ver[2], 0):
        raise RuntimeError(f"version_info.txt 版本不一致: filevers={filevers}, prodvers={prodvers}")
    if f"'{vstr}.0'" not in info:
        raise RuntimeError(f"version_info.txt 缺少 FileVersion/ProductVersion 字符串 {vstr}.0")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python tools/sync_version.py <tag>,tag 形如 v1.2.3")
    ver = parse_tag(sys.argv[1])
    write_version_py(ver)
    write_version_info(ver)
    verify(ver)
    print(f"synced: {CLIENT_DIR.name} 版本单源 -> v{ver[0]}.{ver[1]}.{ver[2]}")


if __name__ == "__main__":
    main()
