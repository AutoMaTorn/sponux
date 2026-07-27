"""Build a PEP 427 wheel for sponux without a build backend.

Debian's python3 here ships without pip/setuptools, and installing them is not
worth it for a pure-Python package: a wheel is just a zip with a dist-info
directory, so we assemble it directly.
"""
import base64
import csv
import hashlib
import io
import os
import time
import zipfile

import metadata as m

DIST = f"{m.NAME}-{m.VERSION}.dist-info"
OUT = m.DIST / f"{m.NAME}-{m.VERSION}-py3-none-any.whl"

METADATA = f"""Metadata-Version: 2.1
Name: {m.NAME}
Version: {m.VERSION}
Summary: {m.SUMMARY}
Requires-Python: {m.REQUIRES_PYTHON}
""" + "".join(
    f"Classifier: {c}\n" for c in m.CLASSIFIERS
) + """

See README.md. Requires PyGObject and GTK 4 from the system package manager:
apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
"""

WHEEL = """Wheel-Version: 1.0
Generator: sponux-manual-build
Root-Is-Purelib: true
Tag: py3-none-any
"""

ENTRY_POINTS = "[console_scripts]\nsponux = sponux.__main__:main\n"


# Everything else in a zip entry is content; the timestamp is not, and
# writestr(str, ...) stamps "now". Two builds a second apart then differ, which
# is the whole reproducibility claim gone — and CI compares the wheel too. The
# .deb and the tarball already pin their timestamps to packaging/changelog.
STAMP = time.gmtime(m.SOURCE_DATE_EPOCH)[:6]


def add(z, rel, data):
    """One zip entry, with nothing in it that depends on when it was built."""
    info = zipfile.ZipInfo(rel, date_time=STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16  # umask must not reach the artifact either
    z.writestr(info, data)


def digest(data):
    h = hashlib.sha256(data).digest()
    b = base64.urlsafe_b64encode(h).rstrip(b"=").decode()
    return f"sha256={b}", len(data)


m.check_version_agreement()
os.makedirs(m.DIST, exist_ok=True)
records = []
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for rel in m.payload():
        data = (m.SRC / rel).read_bytes()
        add(z, rel, data)
        records.append([rel, *digest(data)])
    for rel, text in ((f"{DIST}/METADATA", METADATA), (f"{DIST}/WHEEL", WHEEL),
                      (f"{DIST}/entry_points.txt", ENTRY_POINTS)):
        data = text.encode()
        add(z, rel, data)
        records.append([rel, *digest(data)])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in records:
        w.writerow(row)
    w.writerow([f"{DIST}/RECORD", "", ""])
    add(z, f"{DIST}/RECORD", buf.getvalue().encode())

print(f"built {OUT}")
print(f"  {len(records) + 1} files, {os.path.getsize(OUT)} bytes")
