#!/bin/sh
CONSOLE_USER=`/usr/bin/stat -f%Su /dev/console`
/usr/bin/sudo -u $CONSOLE_USER /usr/bin/killall MozkeyIbGConverter > /dev/null
/usr/bin/sudo -u $CONSOLE_USER /usr/bin/killall MozkeyIbGRenderer > /dev/null
/usr/bin/sudo -u $CONSOLE_USER /usr/bin/killall MozkeyIbG > /dev/null
/usr/bin/true
