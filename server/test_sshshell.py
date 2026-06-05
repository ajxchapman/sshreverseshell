#!/usr/bin/env python3
"""Tests for sshshell.py"""

import hashlib
import io
import os
import select as _select
import signal
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sshshell


class FakeFD:
    """Wraps a raw file descriptor to satisfy fileno() calls."""
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


def read_timeout(fd, size=4096, timeout=2):
    """Read from a file descriptor with a timeout."""
    ready, _, _ = _select.select([fd], [], [], timeout)
    if ready:
        return os.read(fd, size)
    return b""


# ---------------------------------------------------------------------------
# Embedded script generation
# ---------------------------------------------------------------------------

class TestPtyScript(unittest.TestCase):
    def test_substitutes_user(self):
        script = sshshell.pty_script("myuser")
        self.assertIn("grep myuser", script)
        self.assertIn("myuser@", script)

    def test_contains_pty_spawn_methods(self):
        script = sshshell.pty_script("rshell")
        self.assertIn("python3", script)
        self.assertIn("python2", script)
        self.assertIn("script /dev/null", script)

    def test_ends_with_disconnect_and_exit(self):
        script = sshshell.pty_script("rshell")
        lines = script.strip().splitlines()
        self.assertIn("disconnect", lines[-2])
        self.assertEqual("exit", lines[-1])


class TestRcScript(unittest.TestCase):
    def test_sets_term(self):
        self.assertIn("TERM=xterm-256color", sshshell.rc_script(24, 80))

    def test_defines_transfer_function(self):
        script = sshshell.rc_script(24, 80)
        self.assertIn("transfer()", script)
        self.assertIn("$SSH_COMMAND transfer", script)

    def test_runs_id(self):
        self.assertIn("\nid\n", sshshell.rc_script(24, 80))

    def test_injects_terminal_dimensions(self):
        script = sshshell.rc_script(50, 120)
        self.assertIn("stty rows 50 columns 120", script)

    def test_different_dimensions(self):
        self.assertIn("stty rows 24 columns 80", sshshell.rc_script(24, 80))
        self.assertIn("stty rows 100 columns 200", sshshell.rc_script(100, 200))


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

class TestAddKey(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = os.path.join(self.tmpdir, "home", "testuser")
        os.makedirs(self.home)
        self.pw = MagicMock()
        self.pw.pw_dir = self.home
        self.pw.pw_uid = os.getuid()
        self.pw.pw_gid = os.getgid()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _auth_keys_path(self):
        return os.path.join(self.home, ".ssh", "authorized_keys")

    def _read_auth_keys(self):
        with open(self._auth_keys_path()) as f:
            return f.read()

    @patch("sshshell.pwd.getpwnam")
    def test_creates_ssh_dir_with_correct_permissions(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa AAAA user@host")
        ssh_dir = os.path.join(self.home, ".ssh")
        self.assertTrue(os.path.isdir(ssh_dir))
        self.assertEqual(os.stat(ssh_dir).st_mode & 0o777, 0o700)

    @patch("sshshell.pwd.getpwnam")
    def test_authorized_keys_permissions(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa AAAA user@host")
        self.assertEqual(os.stat(self._auth_keys_path()).st_mode & 0o777, 0o600)

    @patch("sshshell.pwd.getpwnam")
    def test_writes_key_with_forced_command_and_restrictions(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa AAAA user@host")
        content = self._read_auth_keys()
        self.assertIn("ssh-rsa AAAA user@host", content)
        self.assertIn("command=", content)
        for restriction in ("no-agent-forwarding", "no-pty",
                            "no-port-forwarding", "no-user-rc",
                            "no-X11-forwarding"):
            self.assertIn(restriction, content)

    @patch("sshshell.pwd.getpwnam")
    def test_key_id_comment_line(self, mock_pw):
        mock_pw.return_value = self.pw
        pubkey = "ssh-rsa AAAA user@host"
        sshshell.add_key("testuser", pubkey)
        key_id = hashlib.sha1(pubkey.encode()).hexdigest()
        self.assertIn(f"SSHREVERSE_{key_id}", self._read_auth_keys())

    @patch("sshshell.pwd.getpwnam")
    def test_duplicate_key_rejected(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa AAAA user@host")
        sshshell.add_key("testuser", "ssh-rsa AAAA user@host")
        self.assertEqual(self._read_auth_keys().count("ssh-rsa AAAA user@host"), 1)

    @patch("sshshell.pwd.getpwnam")
    def test_multiple_different_keys(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa KEY1 u1@h")
        sshshell.add_key("testuser", "ssh-rsa KEY2 u2@h")
        content = self._read_auth_keys()
        self.assertIn("ssh-rsa KEY1 u1@h", content)
        self.assertIn("ssh-rsa KEY2 u2@h", content)

    @patch("sshshell.pwd.getpwnam")
    def test_key_from_file(self, mock_pw):
        mock_pw.return_value = self.pw
        keyfile = os.path.join(self.tmpdir, "id_rsa.pub")
        with open(keyfile, "w") as f:
            f.write("ssh-rsa FROMFILE user@host\n")
        sshshell.add_key("testuser", keyfile)
        self.assertIn("ssh-rsa FROMFILE user@host", self._read_auth_keys())


class TestRemoveKey(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = os.path.join(self.tmpdir, "home", "testuser")
        os.makedirs(os.path.join(self.home, ".ssh"))
        self.pw = MagicMock()
        self.pw.pw_dir = self.home
        self.pw.pw_uid = os.getuid()
        self.pw.pw_gid = os.getgid()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _auth_keys_path(self):
        return os.path.join(self.home, ".ssh", "authorized_keys")

    def _read_auth_keys(self):
        with open(self._auth_keys_path()) as f:
            return f.read()

    @patch("sshshell.pwd.getpwnam")
    def test_removes_key_and_comment(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa AAAA user@host")
        sshshell.remove_key("testuser", "ssh-rsa AAAA user@host")
        content = self._read_auth_keys()
        self.assertNotIn("ssh-rsa AAAA user@host", content)
        self.assertNotIn("SSHREVERSE_", content)

    @patch("sshshell.pwd.getpwnam")
    def test_preserves_other_keys(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.add_key("testuser", "ssh-rsa KEY1 u1@h")
        sshshell.add_key("testuser", "ssh-rsa KEY2 u2@h")
        sshshell.remove_key("testuser", "ssh-rsa KEY1 u1@h")
        content = self._read_auth_keys()
        self.assertNotIn("KEY1", content)
        self.assertIn("ssh-rsa KEY2 u2@h", content)

    @patch("sshshell.pwd.getpwnam")
    def test_nonexistent_key_no_error(self, mock_pw):
        mock_pw.return_value = self.pw
        open(self._auth_keys_path(), "w").close()
        sshshell.remove_key("testuser", "ssh-rsa NOPE user@host")

    @patch("sshshell.pwd.getpwnam")
    def test_missing_authorized_keys_no_error(self, mock_pw):
        mock_pw.return_value = self.pw
        sshshell.remove_key("testuser", "ssh-rsa NOPE user@host")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

class TestHandleDisconnect(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_dir = os.path.join(self.tmpdir, "sshshell")
        os.makedirs(self.session_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _touch(self, name):
        path = os.path.join(self.session_dir, name)
        open(path, "w").close()
        return path

    def test_removes_matching_ip_sessions(self):
        p1 = self._touch("10.0.0.5_18000")
        p2 = self._touch("10.0.0.5_18001")
        other = self._touch("10.0.0.99_18000")

        with patch("sshshell.SESSION_DIR", self.session_dir), \
             patch.dict(os.environ, {"SSH_CLIENT": "10.0.0.5 12345 22"}):
            sshshell.handle_disconnect()

        self.assertFalse(os.path.exists(p1))
        self.assertFalse(os.path.exists(p2))
        self.assertTrue(os.path.exists(other))

    def test_no_ssh_client_is_noop(self):
        self._touch("10.0.0.5_18000")
        with patch("sshshell.SESSION_DIR", self.session_dir), \
             patch.dict(os.environ, {}, clear=True):
            sshshell.handle_disconnect()
        self.assertTrue(os.path.exists(os.path.join(self.session_dir, "10.0.0.5_18000")))

    def test_missing_session_dir_is_noop(self):
        with patch("sshshell.SESSION_DIR", "/nonexistent"), \
             patch.dict(os.environ, {"SSH_CLIENT": "10.0.0.5 12345 22"}):
            sshshell.handle_disconnect()


# ---------------------------------------------------------------------------
# File transfer
# ---------------------------------------------------------------------------

class TestHandleTransfer(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.files_dir = os.path.join(self.tmpdir, "files")
        os.makedirs(self.files_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_saves_file_with_sanitized_name(self):
        with patch("sshshell.FILES_DIR", self.files_dir), \
             patch.dict(os.environ, {
                 "SSH_CLIENT": "10.0.0.1 12345 22",
                 "SSH_ORIGINAL_COMMAND": "transfer /etc/passwd",
             }), \
             patch("sys.stdin", MagicMock(buffer=io.BytesIO(b"file content"))):
            sshshell.handle_transfer()

        filepath = os.path.join(self.files_dir, "10.0.0.1", "_etc_passwd")
        self.assertTrue(os.path.exists(filepath))
        with open(filepath, "rb") as f:
            self.assertEqual(f.read(), b"file content")

    def test_dedup_appends_counter(self):
        client_dir = os.path.join(self.files_dir, "10.0.0.1")
        os.makedirs(client_dir)
        open(os.path.join(client_dir, "_etc_passwd"), "w").close()

        with patch("sshshell.FILES_DIR", self.files_dir), \
             patch.dict(os.environ, {
                 "SSH_CLIENT": "10.0.0.1 12345 22",
                 "SSH_ORIGINAL_COMMAND": "transfer /etc/passwd",
             }), \
             patch("sys.stdin", MagicMock(buffer=io.BytesIO(b"second"))):
            sshshell.handle_transfer()

        self.assertTrue(os.path.exists(os.path.join(client_dir, "_etc_passwd.1")))

    def test_dedup_increments_counter(self):
        client_dir = os.path.join(self.files_dir, "10.0.0.1")
        os.makedirs(client_dir)
        open(os.path.join(client_dir, "_etc_passwd"), "w").close()
        open(os.path.join(client_dir, "_etc_passwd.1"), "w").close()

        with patch("sshshell.FILES_DIR", self.files_dir), \
             patch.dict(os.environ, {
                 "SSH_CLIENT": "10.0.0.1 12345 22",
                 "SSH_ORIGINAL_COMMAND": "transfer /etc/passwd",
             }), \
             patch("sys.stdin", MagicMock(buffer=io.BytesIO(b"third"))):
            sshshell.handle_transfer()

        self.assertTrue(os.path.exists(os.path.join(client_dir, "_etc_passwd.2")))

    def test_no_filename_uses_timestamp(self):
        with patch("sshshell.FILES_DIR", self.files_dir), \
             patch.dict(os.environ, {
                 "SSH_CLIENT": "10.0.0.1 12345 22",
                 "SSH_ORIGINAL_COMMAND": "transfer",
             }), \
             patch("sys.stdin", MagicMock(buffer=io.BytesIO(b"data"))):
            sshshell.handle_transfer()

        client_dir = os.path.join(self.files_dir, "10.0.0.1")
        files = os.listdir(client_dir)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("output_"))


# ---------------------------------------------------------------------------
# Bidirectional forwarding
# ---------------------------------------------------------------------------

class TestForwardRawResize(unittest.TestCase):
    def test_sigwinch_sends_stty_to_socket(self):
        """SIGWINCH triggers a stty resize command through the socket."""
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        a, b = socket.socketpair()

        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

        received = bytearray()

        def stimulate():
            time.sleep(0.3)
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
            os.kill(os.getpid(), signal.SIGWINCH)
            time.sleep(0.3)
            b.settimeout(1)
            try:
                while True:
                    chunk = b.recv(4096)
                    if not chunk:
                        break
                    received.extend(chunk)
            except socket.timeout:
                pass
            b.close()

        t = threading.Thread(target=stimulate, daemon=True)
        t.start()

        with patch("sys.stdin", FakeFD(master)), \
             patch("sys.stdout", FakeFD(master)), \
             patch("os.system"):
            sshshell.forward_raw(a)

        t.join(timeout=3)
        self.assertIn(b"stty rows 50 columns 200", bytes(received))

        os.close(master)
        os.close(slave)


class TestForward(unittest.TestCase):
    def test_socket_to_stdout(self):
        a, b = socket.socketpair()
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        def run():
            with patch("sys.stdin", FakeFD(stdin_r)), \
                 patch("sys.stdout", FakeFD(stdout_w)):
                sshshell.forward(a)

        t = threading.Thread(target=run, daemon=True)
        t.start()

        b.sendall(b"hello from socket")
        data = read_timeout(stdout_r)
        self.assertEqual(data, b"hello from socket")

        b.close()
        os.close(stdin_w)
        t.join(timeout=3)
        for fd in (stdin_r, stdout_r, stdout_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_stdin_to_socket(self):
        a, b = socket.socketpair()
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        def run():
            with patch("sys.stdin", FakeFD(stdin_r)), \
                 patch("sys.stdout", FakeFD(stdout_w)):
                sshshell.forward(a)

        t = threading.Thread(target=run, daemon=True)
        t.start()

        os.write(stdin_w, b"hello from terminal")
        time.sleep(0.2)
        data = b.recv(4096)
        self.assertEqual(data, b"hello from terminal")

        b.close()
        os.close(stdin_w)
        t.join(timeout=3)
        for fd in (stdin_r, stdout_r, stdout_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_socket_close_triggers_exit(self):
        a, b = socket.socketpair()
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        def run():
            with patch("sys.stdin", FakeFD(stdin_r)), \
                 patch("sys.stdout", FakeFD(stdout_w)):
                sshshell.forward(a)

        t = threading.Thread(target=run, daemon=True)
        t.start()

        b.close()
        t.join(timeout=3)
        self.assertFalse(t.is_alive())

        os.close(stdin_w)
        for fd in (stdin_r, stdout_r, stdout_w):
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Full connection handler integration
# ---------------------------------------------------------------------------

class TestHandleConnection(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_dir = os.path.join(self.tmpdir, "sshshell")
        os.makedirs(self.session_dir)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_full_session_lifecycle(self):
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        def run():
            with patch("sshshell.SESSION_DIR", self.session_dir), \
                 patch("sshshell.BASE_PORT", self.port), \
                 patch.dict(os.environ, {"SSH_CLIENT": "10.0.0.1 12345 22"}), \
                 patch("sys.stdin", FakeFD(stdin_r)), \
                 patch("sys.stdout", FakeFD(stdout_w)), \
                 patch("sshshell.signal.signal"):
                sshshell.handle_connection()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        session_file = os.path.join(self.session_dir, f"10.0.0.1_{self.port}")
        deadline = time.time() + 5
        while not os.path.exists(session_file) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(session_file), "Session file not created")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        os.write(stdin_w, b"shell prompt$ ")
        data = read_timeout(sock.fileno())
        self.assertEqual(data, b"shell prompt$ ")

        sock.sendall(b"whoami\n")
        data = read_timeout(stdout_r)
        self.assertEqual(data, b"whoami\n")

        sock.close()
        t.join(timeout=3)
        self.assertFalse(os.path.exists(session_file), "Session file not cleaned up")

        for fd in (stdin_r, stdin_w, stdout_r, stdout_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_session_file_contains_env(self):
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        def run():
            with patch("sshshell.SESSION_DIR", self.session_dir), \
                 patch("sshshell.BASE_PORT", self.port), \
                 patch.dict(os.environ, {"SSH_CLIENT": "10.0.0.1 12345 22"}), \
                 patch("sys.stdin", FakeFD(stdin_r)), \
                 patch("sys.stdout", FakeFD(stdout_w)), \
                 patch("sshshell.signal.signal"):
                sshshell.handle_connection()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        session_file = os.path.join(self.session_dir, f"10.0.0.1_{self.port}")
        deadline = time.time() + 5
        while not os.path.exists(session_file) and time.time() < deadline:
            time.sleep(0.05)

        with open(session_file) as f:
            content = f.read()
        self.assertIn("SSH_CLIENT=10.0.0.1 12345 22", content)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))
        sock.close()
        t.join(timeout=3)

        for fd in (stdin_r, stdin_w, stdout_r, stdout_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_port_skips_occupied_sessions(self):
        open(os.path.join(self.session_dir, f"10.0.0.1_{self.port}"), "w").close()
        open(os.path.join(self.session_dir, f"10.0.0.1_{self.port + 1}"), "w").close()

        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        def run():
            with patch("sshshell.SESSION_DIR", self.session_dir), \
                 patch("sshshell.BASE_PORT", self.port), \
                 patch.dict(os.environ, {"SSH_CLIENT": "10.0.0.1 12345 22"}), \
                 patch("sys.stdin", FakeFD(stdin_r)), \
                 patch("sys.stdout", FakeFD(stdout_w)), \
                 patch("sshshell.signal.signal"):
                sshshell.handle_connection()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        expected = os.path.join(self.session_dir, f"10.0.0.1_{self.port + 2}")
        deadline = time.time() + 5
        while not os.path.exists(expected) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(expected), "Did not skip to port+2")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port + 2))
        sock.close()
        t.join(timeout=3)

        for fd in (stdin_r, stdin_w, stdout_r, stdout_w):
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Operator shell - session listing and selection
# ---------------------------------------------------------------------------

class TestOperatorShell(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_dir = os.path.join(self.tmpdir, "sshshell")
        self.files_dir = os.path.join(self.session_dir, "files")
        os.makedirs(self.files_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_no_sessions_prints_message(self):
        with patch("sshshell.SESSION_DIR", self.session_dir), \
             patch("sshshell.FILES_DIR", self.files_dir), \
             patch("builtins.print") as mock_print:
            sshshell.operator_shell("rshell", simple=False)
        mock_print.assert_called_with("No current connections")

    def test_directories_excluded_from_listing(self):
        open(os.path.join(self.session_dir, "10.0.0.1_18000"), "w").close()
        sessions = sorted(
            e for e in os.listdir(self.session_dir)
            if os.path.isfile(os.path.join(self.session_dir, e))
        )
        self.assertEqual(sessions, ["10.0.0.1_18000"])
        self.assertNotIn("files", sessions)

    def test_invalid_choice_rejected(self):
        open(os.path.join(self.session_dir, "10.0.0.1_18000"), "w").close()
        open(os.path.join(self.session_dir, "10.0.0.2_18001"), "w").close()

        with patch("sshshell.SESSION_DIR", self.session_dir), \
             patch("sshshell.FILES_DIR", self.files_dir), \
             patch("builtins.input", return_value="abc"), \
             patch("builtins.print") as mock_print:
            sshshell.operator_shell("rshell", simple=False)
        mock_print.assert_any_call("Not a number 'abc'")

    def test_out_of_range_choice_rejected(self):
        open(os.path.join(self.session_dir, "10.0.0.1_18000"), "w").close()
        open(os.path.join(self.session_dir, "10.0.0.2_18001"), "w").close()

        with patch("sshshell.SESSION_DIR", self.session_dir), \
             patch("sshshell.FILES_DIR", self.files_dir), \
             patch("builtins.input", return_value="5"), \
             patch("builtins.print") as mock_print:
            sshshell.operator_shell("rshell", simple=False)
        mock_print.assert_any_call("Invalid connection number '5'")

    def test_zero_choice_rejected(self):
        open(os.path.join(self.session_dir, "10.0.0.1_18000"), "w").close()
        open(os.path.join(self.session_dir, "10.0.0.2_18001"), "w").close()

        with patch("sshshell.SESSION_DIR", self.session_dir), \
             patch("sshshell.FILES_DIR", self.files_dir), \
             patch("builtins.input", return_value="0"), \
             patch("builtins.print") as mock_print:
            sshshell.operator_shell("rshell", simple=False)
        mock_print.assert_any_call("Invalid connection number '0'")


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestArgParsing(unittest.TestCase):
    @patch("sshshell.add_key")
    @patch("sshshell.pwd.getpwnam")
    @patch.dict(os.environ, {}, clear=False)
    def test_add_flag(self, mock_pw, mock_add):
        mock_pw.return_value = MagicMock()
        with patch("sys.argv", ["sshshell.py", "-a", "ssh-rsa", "AAAA", "user@host"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}, clear=False):
            sshshell.main()
        mock_add.assert_called_once_with("rshell", "ssh-rsa AAAA user@host")

    @patch("sshshell.remove_key")
    @patch("sshshell.pwd.getpwnam")
    def test_remove_flag(self, mock_pw, mock_rm):
        mock_pw.return_value = MagicMock()
        with patch("sys.argv", ["sshshell.py", "-r", "ssh-rsa", "AAAA", "user@host"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}, clear=False):
            sshshell.main()
        mock_rm.assert_called_once_with("rshell", "ssh-rsa AAAA user@host")

    @patch("sshshell.handle_connection")
    def test_connection_flag(self, mock_conn):
        with patch("sys.argv", ["sshshell.py", "-c"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}, clear=False):
            sshshell.main()
        mock_conn.assert_called_once()

    @patch("sshshell.add_key")
    @patch("sshshell.pwd.getpwnam")
    def test_user_flag_before_add(self, mock_pw, mock_add):
        mock_pw.return_value = MagicMock()
        with patch("sys.argv", ["sshshell.py", "-u", "myuser", "-a", "ssh-rsa", "AAAA"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}, clear=False):
            sshshell.main()
        mock_add.assert_called_once_with("myuser", "ssh-rsa AAAA")

    @patch("sshshell.handle_transfer")
    def test_transfer_via_ssh_original_command(self, mock_transfer):
        with patch("sys.argv", ["sshshell.py", "-c"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "transfer /etc/passwd"}):
            sshshell.main()
        mock_transfer.assert_called_once()

    @patch("sshshell.handle_disconnect")
    def test_disconnect_via_ssh_original_command(self, mock_disconnect):
        with patch("sys.argv", ["sshshell.py", "-c"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "disconnect"}):
            sshshell.main()
        mock_disconnect.assert_called_once()

    @patch("sshshell.pwd.getpwnam", side_effect=KeyError("nosuchuser"))
    def test_nonexistent_user_exits_gracefully(self, mock_pw):
        with patch("sys.argv", ["sshshell.py", "-a", "key"]), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}, clear=False), \
             patch("builtins.print") as mock_print:
            sshshell.main()
        mock_print.assert_called_with("Remote shell user 'rshell' does not exist, exiting...")


if __name__ == "__main__":
    unittest.main()
