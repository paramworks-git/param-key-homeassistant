"""Public-repository guard (CI, every push): refuses real MAC addresses, and metadata in images and
PDFs, in any tracked file. Secrets are gitleaks' job (ci.yml). Private names are checked by a local
pre-commit hook instead, because the list of them is itself private.

Needs qpdf for PDFs (their text sits in compressed streams).
"""

from pathlib import Path
import re
import subprocess
import sys

MAC = re.compile(rb"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
# Placeholders the docs, configs and tests use on purpose; 02:00:00:00:00:xx is the
# locally administered example range.
MAC_OK = {b"FF:FF:FF:FF:FF:FF", b"AA:BB:CC:DD:EE:FF", b"00:00:00:00:00:00"}
PNG_META = (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME")
PDF_META = re.compile(rb"/(Author|Creator|Producer|Keywords|CreationDate|ModDate)\s*[(<]")


def png_chunks(data: bytes):
    i = 8
    while i + 8 <= len(data):
        length = int.from_bytes(data[i:i + 4], "big")
        yield data[i + 4:i + 8]
        i += 12 + length


def problems_in(path: Path) -> list[str]:
    data = path.read_bytes()
    found = []
    for m in sorted(set(MAC.findall(data))):
        if m.upper() not in MAC_OK and not m.upper().startswith(b"02:00:00:00:00:"):
            found.append(f"MAC address {m.decode()}")
    suffix = path.suffix.lower()
    if suffix == ".png" and any(c in PNG_META for c in png_chunks(data)):
        found.append("PNG metadata")
    if suffix in (".jpg", ".jpeg") and b"Exif\x00\x00" in data:
        found.append("EXIF metadata")
    if suffix == ".pdf":
        text = subprocess.run(["qpdf", "--qdf", "--object-streams=disable", str(path), "-"],
                              capture_output=True, check=True).stdout
        if PDF_META.search(data + text):
            found.append("PDF metadata")
    return found


def main() -> None:
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    bad = []
    for name in filter(None, files):
        path = Path(name.decode())
        if path.is_file():
            bad += [f"{path}: {p}" for p in problems_in(path)]
    for line in bad:
        print(line)
    print(f"{len(files) - 1} files checked, {len(bad)} problem(s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
