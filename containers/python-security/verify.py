"""Verify the installed backports and exploit regressions; emit scan evidence."""

import hashlib
import io
import json
import poplib
import sys
import sysconfig
import tarfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import Mock


class BackportRegressionTests(unittest.TestCase):
    def test_pop3_rejects_control_characters_before_sending(self):
        client = poplib.POP3.__new__(poplib.POP3)
        client.encoding = "utf-8"
        client._debugging = 0
        client._putline = Mock()
        for value in [*range(32), 127]:
            with self.subTest(character=value), self.assertRaises(ValueError):
                client._putcmd(f"USER alice{chr(value)}PASS injected")
        client._putline.assert_not_called()
        client._putcmd("USER alice")
        client._putline.assert_called_once_with(b"USER alice")

    def test_idna_uses_unicode_3_2_case_folding(self):
        cases = [
            ("\N{CHEROKEE LETTER A}\N{CHEROKEE LETTER A}", b"xn--58da"),
            ("\N{GEORGIAN CAPITAL LETTER AN}.", b"xn--7md."),
            ("\N{CYRILLIC LETTER PALOCHKA}.example", b"xn--d5a.example"),
            ("\N{ROMAN NUMERAL REVERSED ONE HUNDRED}.example.", b"xn--q5g.example."),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(name.encode("idna"), expected)

    def test_password_manager_does_not_downgrade_credentials(self):
        for manager_type in (
            urllib.request.HTTPPasswordMgr,
            urllib.request.HTTPPasswordMgrWithPriorAuth,
        ):
            with self.subTest(manager=manager_type.__name__):
                manager = manager_type()
                manager.add_password("realm", "https://example.test/", "alice", "secret")
                self.assertEqual(
                    manager.find_user_password("realm", "https://example.test/path"),
                    ("alice", "secret"),
                )
                self.assertEqual(
                    manager.find_user_password("realm", "http://example.test/path"),
                    (None, None),
                )

    def test_zip_decompression_bounds_output_and_preserves_contents(self):
        payload = b"\0" * (4 * 1024 * 1024)
        for compression in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, zipfile.ZIP_ZSTANDARD):
            with self.subTest(compression=compression):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
                    archive.writestr("workflow.yaml", payload)
                with zipfile.ZipFile(buffer) as archive, archive.open("workflow.yaml") as member:
                    self.assertLessEqual(len(member._read1(100)), member.MIN_READ_SIZE)
                with zipfile.ZipFile(buffer) as archive, archive.open("workflow.yaml") as member:
                    self.assertEqual(member.read(), payload)

    def test_tar_filters_normalize_leave_and_return_paths(self):
        member = tarfile.TarInfo("../outside/../destination/sub/file")
        for filter_function in (tarfile.tar_filter, tarfile.data_filter):
            with self.subTest(filter=filter_function.__name__):
                filtered = filter_function(member, "/backport-test/destination")
                self.assertEqual(filtered.name, "../destination/sub/file")

    def test_tar_link_fallback_honors_filter_rejection(self):
        with tarfile.TarFile(fileobj=io.BytesIO(), mode="w") as archive:
            link = tarfile.TarInfo("link")
            link.type = tarfile.LNKTYPE
            link._link_target = "/backport-test/missing-target"
            target = tarfile.TarInfo("target")
            archive._find_link_target = Mock(return_value=target)
            archive._extract_member = Mock()
            filter_function = Mock(
                side_effect=lambda member, _: None if member.name == "link" else member
            )
            archive.makelink_with_filter(
                link, "/backport-test/link", filter_function, "/backport-test"
            )
            archive._extract_member.assert_not_called()
            self.assertEqual(filter_function.call_count, 1)


def verify(manifest: dict) -> dict:
    if sys.version.split()[0] != manifest["python_version"]:
        raise RuntimeError("Unsupported Python version for backport evidence")
    stdlib = Path(sysconfig.get_path("stdlib"))
    actual = {}
    for name, hashes in manifest["files"].items():
        actual[name] = hashlib.sha256((stdlib / name).read_bytes()).hexdigest()
        if actual[name] != hashes["patched"]:
            raise RuntimeError(f"Security backport missing or changed: {name}")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BackportRegressionTests)
    if not unittest.TextTestRunner(stream=sys.stderr).run(suite).wasSuccessful():
        raise RuntimeError("Installed Python security regression tests failed")
    library = Path(sysconfig.get_config_var("LIBDIR")) / sysconfig.get_config_var("LDLIBRARY")
    library = library.resolve(strict=True)
    if not library.is_file():
        raise RuntimeError("Python shared library is not a regular file")
    return {
        "library": str(library),
        "python_version": manifest["python_version"],
        "files": actual,
        "cves": manifest["cves"],
        "executable": str(Path(sys.executable).resolve()),
        "patches": [item["commit"] for item in manifest["patches"]],
    }


if __name__ == "__main__":
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    evidence = verify(manifest)
    if len(sys.argv) == 2:
        evidence["image"] = sys.argv[1]
    print(json.dumps(evidence, sort_keys=True))
