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
        z.writestr(rel, data)
        records.append([rel, *digest(data)])
    for rel, text in ((f"{DIST}/METADATA", METADATA), (f"{DIST}/WHEEL", WHEEL),
                      (f"{DIST}/entry_points.txt", ENTRY_POINTS)):
        data = text.encode()
        z.writestr(rel, data)
        records.append([rel, *digest(data)])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in records:
        w.writerow(row)
    w.writerow([f"{DIST}/RECORD", "", ""])
    z.writestr(f"{DIST}/RECORD", buf.getvalue())

print(f"built {OUT}")
print(f"  {len(records) + 1} files, {os.path.getsize(OUT)} bytes")
