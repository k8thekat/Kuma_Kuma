#!/bin/bash
SERVICE="Kuma_Reddit.py"
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
echo $SCRIPT_DIR
cd $SCRIPT_DIR
if test -f ${SERVICE}.pid; then
    if ps -p `cat ${SERVICE}.pid` >/dev/null; then 
        echo "${SERVICE} is already running"
        exit 0
    fi
fi

python3 -u ./${SERVICE} > ${SERVICE}.log 2>&1 &
echo $! > ${SERVICE}.pid
echo "${SERVICE} is starting"
