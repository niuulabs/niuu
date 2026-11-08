"""Verify the installed backports and exploit regressions; emit scan evidence."""

import hashlib
import http.client
import inspect
import io
import json
import poplib
import socket
import sys
import sysconfig
import tarfile
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import Mock


class FakeSocket:
    """Serve a canned HTTP response to http.client without any network."""

    def __init__(self, data: bytes):
        self._data = data

    def makefile(self, mode):
        return io.BytesIO(self._data)


def http_response(head: str, body: bytes) -> http.client.HTTPResponse:
    response = http.client.HTTPResponse(FakeSocket(head.encode() + body), method="GET")
    response.begin()
    return response


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

    def test_http_content_length_follows_the_rfc_9112_grammar(self):
        # CVE-2026-103995: int() also accepts a sign, underscores and spaces, so
        # these must not frame the body; optional whitespace around a valid
        # value is not part of it and must still frame the body.
        for value in ("+5", "5_0", "5 0", "-5", "0x5"):
            with self.subTest(value=value):
                response = http_response(
                    f"HTTP/1.1 200 OK\r\nContent-Length: {value}\r\n\r\n", b"Hello\r\n"
                )
                self.assertIsNone(response.length)
                self.assertEqual(response.read(), b"Hello\r\n")
        for value in ("7", "7 ", "7\t", "7 \t ", " 7 "):
            with self.subTest(value=value):
                response = http_response(
                    f"HTTP/1.1 200 OK\r\nContent-Length: {value}\r\n\r\n", b"Hello\r\nextra"
                )
                self.assertEqual(response.length, 7)
                self.assertEqual(response.read(), b"Hello\r\n")
                self.assertTrue(response.isclosed())

    def test_http_chunk_size_follows_the_rfc_9112_grammar(self):
        # CVE-2026-103995: a chunk-size is 1*HEXDIG; int(_, 16) accepts more.
        head = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        for size in ("-5", "+5", "0x5", "1_f", " 5"):
            with self.subTest(size=size):
                response = http_response(head, f"{size}\r\nHELLO\r\n0\r\n\r\n".encode())
                with self.assertRaises(http.client.IncompleteRead):
                    response.read()
                self.assertTrue(response.isclosed())
        response = http_response(head, b"5\r\nHELLO\r\n0\r\n\r\n")
        self.assertEqual(response.read(), b"HELLO")

    def test_getaddrinfo_rejects_null_bytes_in_host_and_port(self):
        # CVE-2026-116566: the C layer truncates at a null byte, so
        # "127.0.0.1\0evil" resolved as "127.0.0.1". This guard is in socket.py;
        # the compiled _socket.getaddrinfo is unchanged.
        class HiddenText(str):
            def __contains__(self, item):
                return False

        class HiddenBytes(bytes):
            def __contains__(self, item):
                return False

        for host in (
            "local\0host",
            b"local\0host",
            "localhost\0",
            b"localhost\0",
            "\0localhost",
            "\0",
            b"\0",
            "b\xfccher\0.example",
            HiddenText("local\0host"),
            HiddenBytes(b"local\0host"),
        ):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "host"):
                socket.getaddrinfo(host, 80)
        for port in ("8\x000", b"8\x000", "http\0", b"http\0", "\0", b"\0", HiddenText("8\x000")):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "port"):
                socket.getaddrinfo("localhost", port)
        for port in (80, "80", b"80", None):
            with self.subTest(port=port):
                addresses = socket.getaddrinfo("127.0.0.1", port, type=socket.SOCK_STREAM)
                self.assertTrue(addresses)
                self.assertEqual(addresses[0][4][0], "127.0.0.1")

    def test_other_address_resolution_rejects_null_bytes(self):
        # CVE-2026-116566: getaddrinfo() is the only resolver entry point that
        # truncated. The others already reject a null byte, which is why the
        # guard in socket.getaddrinfo() covers the public API.
        host = "local\0host"
        for function in (
            socket.gethostbyname,
            socket.gethostbyname_ex,
            socket.gethostbyaddr,
            socket.getservbyname,
            socket.inet_aton,
        ):
            with self.subTest(function=function.__name__):
                with self.assertRaises((TypeError, ValueError)):
                    function(host)
        with self.assertRaises((TypeError, ValueError)):
            socket.getnameinfo((host, 80), 0)
        with socket.socket() as client:
            for method in (client.connect, client.connect_ex, client.bind):
                with self.subTest(method=method.__name__):
                    with self.assertRaises((TypeError, ValueError)):
                        method((host, 80))

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

    def test_temporary_directory_cleanup_stays_relative_to_open_directories(self):
        # CVE-2026-12345: cleanup must not resolve paths again, or a directory
        # swapped for a symlink mid-cleanup redirects deletion outside the tree.
        self.assertTrue(tempfile._rmtree_use_dir_fd)
        parameters = inspect.signature(tempfile.TemporaryDirectory._rmtree).parameters
        self.assertIn("dir_fd", parameters)
        self.assertIn("fullname", parameters)


def verify(manifest: dict) -> dict:
    if sys.version.split()[0] != manifest["python_version"]:
        raise RuntimeError("Unsupported Python version for backport evidence")
    stdlib = Path(sysconfig.get_path("stdlib"))
    actual = {}
    for name, hashes in manifest["files"].items():
        actual[name] = hashlib.sha256((stdlib / name).read_bytes()).hexdigest()
        if actual[name] != hashes["patched"]:
            raise RuntimeError(f"Security backport missing or changed: {name}")
    upstream = {}
    for item in manifest["upstream_fixes"]:
        upstream[item["cve"]] = hashlib.sha256((stdlib / item["file"]).read_bytes()).hexdigest()
        if upstream[item["cve"]] != item["sha256"]:
            raise RuntimeError(f"Upstream security fix missing or changed: {item['file']}")
    for item in manifest["mitigations"]:
        if item["file"] not in manifest["files"]:
            raise RuntimeError(f"Mitigation names an unverified module: {item['file']}")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BackportRegressionTests)
    if not unittest.TextTestRunner(stream=sys.stderr).run(suite).wasSuccessful():
        raise RuntimeError("Installed Python security regression tests failed")
    # python-build-standalone keeps its original LIBDIR after bundle relocation.
    library = stdlib.parent / sysconfig.get_config_var("LDLIBRARY")
    library = library.resolve(strict=True)
    if not library.is_file():
        raise RuntimeError("Python shared library is not a regular file")
    return {
        "library": str(library),
        "python_version": manifest["python_version"],
        "files": actual,
        "cves": manifest["cves"],
        "mitigations": [item["cve"] for item in manifest["mitigations"]],
        "executable": str(Path(sys.executable).resolve()),
        "patches": [item["commit"] for item in manifest["patches"]],
        "upstream_fixes": upstream,
    }


if __name__ == "__main__":
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    evidence = verify(manifest)
    if len(sys.argv) == 2:
        evidence["image"] = sys.argv[1]
    print(json.dumps(evidence, sort_keys=True))
