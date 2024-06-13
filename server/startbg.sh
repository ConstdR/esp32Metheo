#!/bin/sh

./web.py 2>>log 1>>log  &
./listenudp.py 2>>log 1>>log  &

/bin/sh
