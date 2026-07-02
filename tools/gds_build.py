# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Minimal, dependency-free GDSII *writer* (Phase G0 of the GDS/OA plan).

Purpose: tests generate their binary GDS inputs deterministically — GDS is a
binary format, so checked-in blobs would violate the repo's diffable-test-data
principle. All timestamps are ZEROED, so identical calls produce identical
bytes. This scaffolding grows into the real exporter (Phase G4).

Coordinates are µm (floats); they are converted to database units (default
dbu = 1 nm) on write. See docs/internal/gds_oa_interchange.md.

Usage:
    b = GdsBuilder(dbu_um=0.001)
    b.structure("leaf").boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)])
    b.structure("top").sref("leaf", (2, 2), inst_name="u0") \
                      .aref("leaf", (10, 0), cols=2, rows=3,
                            col_pitch=(6, 0), row_pitch=(0, 4)) \
                      .text(63, 0, (2.5, 2.5), "net_a")
    b.write("out.gds")
"""

import struct

# Record type/datatype byte pairs (GDSII stream format).
_HEADER, _BGNLIB, _LIBNAME, _UNITS, _ENDLIB = (0x00, 0x02), (0x01, 0x02), \
    (0x02, 0x06), (0x03, 0x05), (0x04, 0x00)
_BGNSTR, _STRNAME, _ENDSTR = (0x05, 0x02), (0x06, 0x06), (0x07, 0x00)
_BOUNDARY, _SREF, _AREF, _TEXT = (0x08, 0x00), (0x0A, 0x00), (0x0B, 0x00), \
    (0x0C, 0x00)
_LAYER, _DATATYPE, _XY, _ENDEL = (0x0D, 0x02), (0x0E, 0x02), (0x10, 0x03), \
    (0x11, 0x00)
_SNAME, _COLROW, _TEXTTYPE, _STRING = (0x12, 0x06), (0x13, 0x02), \
    (0x16, 0x02), (0x19, 0x06)
_STRANS, _MAG, _ANGLE = (0x1A, 0x01), (0x1B, 0x05), (0x1C, 0x05)
_PROPATTR, _PROPVALUE = (0x2B, 0x02), (0x2C, 0x06)
_BOX, _BOXTYPE = (0x2D, 0x00), (0x2E, 0x02)

# The PROPATTR number this toolchain uses to carry an instance name on a ref.
INST_NAME_PROPATTR = 61


def _real8(value: float) -> bytes:
    """Encode an excess-64, base-16 GDSII 8-byte real."""
    if value == 0:
        return b"\x00" * 8
    sign = 0x80 if value < 0 else 0
    v = abs(value)
    exp = 64
    while v >= 1.0:
        v /= 16.0
        exp += 1
    while v < 1.0 / 16.0:
        v *= 16.0
        exp -= 1
    mant = int(v * (1 << 56))
    return bytes([sign | exp]) + mant.to_bytes(7, "big")


def _rec(rt, payload=b"") -> bytes:
    if len(payload) % 2:                       # strings pad to even length
        payload += b"\x00"
    return struct.pack(">HBB", 4 + len(payload), rt[0], rt[1]) + payload


def _ints(fmt, *vals) -> bytes:
    return struct.pack(">" + fmt * len(vals), *vals)


class _Struct:
    def __init__(self, builder, name):
        self._b = builder
        self.name = name
        self._elems = []

    def _xy(self, pts_um):
        d = self._b._dbu_um
        return b"".join(_ints("i", round(x / d), round(y / d))
                        for x, y in pts_um)

    def boundary(self, layer, datatype, pts_um):
        """Closed polygon; the first point is repeated automatically."""
        pts = list(pts_um)
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        self._elems.append(
            _rec(_BOUNDARY) + _rec(_LAYER, _ints("h", layer)) +
            _rec(_DATATYPE, _ints("h", datatype)) +
            _rec(_XY, self._xy(pts)) + _rec(_ENDEL))
        return self

    def _trans(self, mirror, angle, mag):
        out = b""
        if mirror or angle or mag != 1.0:
            out += _rec(_STRANS, _ints("H", 0x8000 if mirror else 0))
            if mag != 1.0:
                out += _rec(_MAG, _real8(mag))
            if angle:
                out += _rec(_ANGLE, _real8(float(angle)))
        return out

    def _prop(self, inst_name):
        if not inst_name:
            return b""
        return (_rec(_PROPATTR, _ints("h", INST_NAME_PROPATTR)) +
                _rec(_PROPVALUE, inst_name.encode()))

    def sref(self, sname, origin_um, *, inst_name="", mirror=False,
             angle=0, mag=1.0):
        self._elems.append(
            _rec(_SREF) + _rec(_SNAME, sname.encode()) +
            self._trans(mirror, angle, mag) +
            _rec(_XY, self._xy([origin_um])) +
            self._prop(inst_name) + _rec(_ENDEL))
        return self

    def aref(self, sname, origin_um, *, cols, rows, col_pitch, row_pitch,
             inst_name="", mirror=False, angle=0, mag=1.0):
        ox, oy = origin_um
        p2 = (ox + col_pitch[0] * cols, oy + col_pitch[1] * cols)
        p3 = (ox + row_pitch[0] * rows, oy + row_pitch[1] * rows)
        self._elems.append(
            _rec(_AREF) + _rec(_SNAME, sname.encode()) +
            self._trans(mirror, angle, mag) +
            _rec(_COLROW, _ints("h", cols, rows)) +
            _rec(_XY, self._xy([origin_um, p2, p3])) +
            self._prop(inst_name) + _rec(_ENDEL))
        return self

    def text(self, layer, texttype, at_um, string):
        self._elems.append(
            _rec(_TEXT) + _rec(_LAYER, _ints("h", layer)) +
            _rec(_TEXTTYPE, _ints("h", texttype)) +
            _rec(_XY, self._xy([at_um])) +
            _rec(_STRING, string.encode()) + _rec(_ENDEL))
        return self

    def structure(self, name):                 # chain to the next structure
        return self._b.structure(name)

    def write(self, path):
        return self._b.write(path)


class GdsBuilder:
    """Builds a deterministic GDSII library (zeroed timestamps)."""

    def __init__(self, libname="BUDALIB", dbu_um=0.001):
        self._libname = libname
        self._dbu_um = float(dbu_um)           # µm per database unit
        self._structs = []

    def structure(self, name) -> _Struct:
        st = _Struct(self, name)
        self._structs.append(st)
        return st

    def to_bytes(self) -> bytes:
        zero12 = _ints("h", *([0] * 12))       # 12 zeroed timestamp shorts
        out = [_rec(_HEADER, _ints("h", 600)),
               _rec(_BGNLIB, zero12),
               _rec(_LIBNAME, self._libname.encode()),
               # UNITS: user-units per dbu (user unit = µm), meters per dbu.
               _rec(_UNITS, _real8(self._dbu_um) + _real8(self._dbu_um * 1e-6))]
        for st in self._structs:
            out.append(_rec(_BGNSTR, zero12))
            out.append(_rec(_STRNAME, st.name.encode()))
            out.extend(st._elems)
            out.append(_rec(_ENDSTR))
        out.append(_rec(_ENDLIB))
        return b"".join(out)

    def write(self, path) -> str:
        with open(path, "wb") as f:
            f.write(self.to_bytes())
        return path
