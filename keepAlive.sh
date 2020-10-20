while true; 
    do 
    python3 main.py >> log.log 2>&1 & 
    pid=$!
    sleep 60s
    kill $pid
    done

#ps aux | grep python3 | grep -v grep | awk '{ print "kill -9", $2 }' | sh