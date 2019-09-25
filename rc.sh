#!/bin/bash


# reset screen-256color
export SHELL=bash
export TERM=xterm-256color
stty rows `tput lines` columns `tput cols`

.transfer() {
  cat $1 | eval $SSH_COMMAND transfer "$1"
}
clear
id
