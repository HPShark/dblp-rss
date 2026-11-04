# 执行命令 nohup python3 health_check.py &


import requests
import os
import time

# Use liveness, not a feed which returns 503 while its first import is pending.
CHECK_URL = "https://xxx.com/health"
DOCKER_CONTAINER = "dblp-rss"

def health_check():
    try:
        response = requests.get(CHECK_URL, timeout=5)
        if response.status_code != 200:
            raise Exception("Service returned non-200 status code")
    except Exception as e:
        print(f"Health check failed: {e}")
        os.system(f"docker restart {DOCKER_CONTAINER}")

if __name__ == "__main__":
    while True:
        health_check()
        time.sleep(600)
