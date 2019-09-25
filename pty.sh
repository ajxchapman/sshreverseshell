#!/bin/sh

SSH_COMMAND=`ps -af | grep rshell | grep -Eo 'ssh .*'`
SSH_IDENTITY=`echo $SSH_COMMAND | grep -Eo "\-i [^ ]+"`
SSH_SERVER=`echo $SSH_COMMAND | grep -Eo "rshell@[^ ]+"`
SSH_COMMAND="ssh -q -o \"StrictHostKeyChecking no\" -o \"UserKnownHostsFile /dev/null\" ${SSH_IDENTITY} ${SSH_SERVER}"
export SSH_COMMAND

{
  which python3 && python3 -c 'import pty; pty.spawn("/bin/bash")' 2>/dev/null
} ||
{
  which python2 && python2 -c 'import pty; pty.spawn("/bin/bash")' 2>/dev/null
} ||
{
  which python && python -c 'import pty; pty.spawn("/bin/bash")' 2>/dev/null
} ||
{
  which script && script /dev/null 2>/dev/null
} ||
echo No pty available

# Attempt a graceful disconnect
eval ${SSH_COMMAND} disconnect
exit
