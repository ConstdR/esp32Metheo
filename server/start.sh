#!/bin/bash

# tmux new-session -d -s weatherweb './startbg.sh'

tmux new-session -d -s WeatherServ './web.py 2>&1 | tee -a log'

tmux a -t WeatherServ
