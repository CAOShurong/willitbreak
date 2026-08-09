"""Archive fetching and extraction security tests."""

from __future__ import annotations

import io
import pathlib
import tarfile
import tempfile
import unittest
import zipfile

from willitbreak.fetch import FetchError, _safe_extract_tar, _safe_extract_zip


class SafeExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = pathlib.Path(self._temp.name)
        self.destination = self.root / "package"
        self.destination.mkdir()

    def test_zip_rejects_same_prefix_sibling_escape(self) -> None:
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("../package-escape/proof.txt", "escaped")

        with self.assertRaisesRegex(FetchError, "escapes its directory"):
            _safe_extract_zip(data.getvalue(), self.destination)

        self.assertFalse((self.root / "package-escape" / "proof.txt").exists())

    def test_tar_rejects_same_prefix_sibling_escape(self) -> None:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            payload = b"escaped"
            member = tarfile.TarInfo("../package-escape/proof.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

        with self.assertRaisesRegex(FetchError, "escapes its directory"):
            _safe_extract_tar(data.getvalue(), self.destination)

        self.assertFalse((self.root / "package-escape" / "proof.txt").exists())

    def test_tar_rejects_non_file_members(self) -> None:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            member = tarfile.TarInfo("device")
            member.type = tarfile.FIFOTYPE
            archive.addfile(member)

        with self.assertRaisesRegex(FetchError, "unsupported entry"):
            _safe_extract_tar(data.getvalue(), self.destination)

    def test_regular_zip_and_tar_members_are_extracted(self) -> None:
        zip_data = io.BytesIO()
        with zipfile.ZipFile(zip_data, "w") as archive:
            archive.writestr("zip/source.py", "ZIP = True\n")
        _safe_extract_zip(zip_data.getvalue(), self.destination)

        tar_data = io.BytesIO()
        with tarfile.open(fileobj=tar_data, mode="w:gz") as archive:
            payload = b"TAR = True\n"
            member = tarfile.TarInfo("tar/source.py")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        _safe_extract_tar(tar_data.getvalue(), self.destination)

        self.assertEqual(
            (self.destination / "zip" / "source.py").read_text(encoding="utf-8"),
            "ZIP = True\n",
        )
        self.assertEqual(
            (self.destination / "tar" / "source.py").read_text(encoding="utf-8"),
            "TAR = True\n",
        )


if __name__ == "__main__":
    unittest.main()
