"""Hand-built, uncompressed synthetic RAR fixtures; never student archives."""
import ctypes.util
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from grader import materials


def header(data):
    return struct.pack("<H", zlib.crc32(data) & 0xffff) + data


def stored_rar(files):
    result = b"Rar!\x1a\x07\x00" + header(struct.pack("<BHHHI", 0x73, 0, 13, 0, 0))
    for name, body, size in files:
        name = name.encode("utf-8")
        core = struct.pack("<BHHIIBIIBBHI", 0x74, 0x8000, 32 + len(name), len(body), size, 3, zlib.crc32(body), 0, 20, 0x30, len(name), 0o100644)
        result += header(core + name) + body
    return result + header(struct.pack("<BHH", 0x7b, 0, 7))


@unittest.skipUnless(ctypes.util.find_library("archive"), "optional system libarchive is unavailable")
class RarTests(unittest.TestCase):
    def test_reads_python_without_executing_or_extracting_paths(self):
        code = b"raise SystemExit('must not execute')"
        data = stored_rar([("../../outside.py", code, len(code))])
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "nested" / "deeper"
            directory.mkdir(parents=True)
            previous = Path.cwd()
            try:
                os.chdir(directory)
                text, notes = materials.extract("submission.rar", data)
            finally:
                os.chdir(previous)
            self.assertEqual([p for p in Path(folder).rglob("*") if p.is_file()], [])
        self.assertIn("must not execute", text)
        self.assertEqual(notes, [])

    def test_declared_uncompressed_size_is_limited_before_reading(self):
        data = stored_rar([("large.py", b"x=1", materials.MAX_ARCHIVE + 1)])
        with self.assertRaisesRegex(ValueError, "大小"):
            materials.extract("submission.rar", data)

    def test_too_many_entries_rejected(self):
        from grader.rar import read_rar
        data = stored_rar([("a.py", b"x=1", 3), ("b.py", b"y=2", 3)])
        with self.assertRaisesRegex(ValueError, "文件数"):
            read_rar(data, max_files=1)

    def test_corrupt_archive_never_becomes_empty_student_work(self):
        with self.assertRaises(ValueError):
            materials.extract("submission.rar", b"not an archive")

