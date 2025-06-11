from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.responses import Response, PlainTextResponse
from dblp import dblp_rss, init_cache_db
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from datetime import datetime

# --- Logging Setup ---
LOG_DIR = "cache"
LOG_FILE = os.path.join(LOG_DIR, "request.log")

# Create a dedicated logger for requests
request_logger = logging.getLogger("request_logger")
request_logger.setLevel(logging.INFO)

# Create a handler that writes to the log file, and a formatter
file_handler = logging.FileHandler(LOG_FILE, mode='a')
formatter = logging.Formatter('%(message)s')
file_handler.setFormatter(formatter)

# Add the handler to the logger
request_logger.addHandler(file_handler)
# --- End Logging Setup ---

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Logs incoming client IP, timestamp, and URL to a file."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    client_ip = request.client.host
    url = str(request.url)
    
    request_logger.info(f"IP: {client_ip}, Time: {timestamp}, URL: {url}")
    
    response = await call_next(request)
    return response

# 优化队列配置，使用更高效的线程池
executor = ThreadPoolExecutor(max_workers=2)  # 限制线程池大小为 2，控制资源使用
active_requests = asyncio.Semaphore(2)  # 设置并发限制为 2

@app.on_event("startup")
async def startup_event():
    """Initializes the cache database and log directory on application startup."""
    os.makedirs(LOG_DIR, exist_ok=True)
    init_cache_db()

@app.get("/dblp/{keyword:path}", response_class=PlainTextResponse)
async def get_dblp_rss(keyword: str = Path(..., regex=r".+")):
    """
    处理 GET 请求，接收 keyword 参数并返回生成的 RSS 数据。
    """
    print(f"Received keyword: {keyword}")  # 调试信息

    loop = asyncio.get_event_loop()
    try:
        # 使用线程池执行耗时任务
        async with active_requests:  # 超过 2 个并发将排队等待
            result = await loop.run_in_executor(executor, dblp_rss, keyword)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 返回 RSS 数据，并指定正确的 Content-Type
    return Response(result, media_type="application/rss+xml")

if __name__ == "__main__":
    import uvicorn
    # 启动 FastAPI 应用，监听 0.0.0.0 和端口 8080
    uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=True)
