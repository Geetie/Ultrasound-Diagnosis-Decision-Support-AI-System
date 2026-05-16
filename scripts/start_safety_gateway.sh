#!/bin/bash
set -e

if [ "$(id -u)" = "0" ]; then
    echo "Setting oom_score_adj to -1000 for OOM protection..."
    echo -1000 > /proc/self/oom_score_adj

    mkdir -p /var/log/supervisor /var/run
    chown -R sguser:sguser /var/log/supervisor /var/run
fi

exec supervisord -c /etc/supervisor/conf.d/safety-gateway.conf
