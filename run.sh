#!/bin/bash

if [[ "$HOSTNAME" == "s022prodlx1" ]]; then
 echo "LX1"
 ./setup_environment_lx1.sh

elif [[ "$HOSTNAME" == "s022prodlx2" ]]; then
 echo "LX2"
 ./setup_environment_lx2.sh

else
 echo "unknown hostname: $HOSTNAME"
 exit 0
fi

#source ./setup_environment.sh
python srs570_ioc.py --mode tcp -p 1,2,3,4 &
#python srs570_ioc_both_log.py -p 1
