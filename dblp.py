import requests
import json
import datetime
import random
from xml.etree import ElementTree as ET
from urllib.parse import quote, unquote
import os
import sqlite3
import re
import time

# How many articles must be pulled
NB_ENTRIES = 300
# How many articles to fetch from DBLP API
FETCH_ENTRIES = 1000
# Cache expiration time in hours
CACHE_EXPIRATION_HOURS = 6
# 每个表最多保存的记录数
MAX_TABLE_RECORDS = 400

CACHE_DB_FILE = "cache/dblp_cache.db"

# 多个User-Agent列表
USER_AGENTS = [
  "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.3",
  "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-A326B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.3",
  "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-G780G) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.3",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/121.0.6167.171 Mobile/15E148 Safari/604",
  "Mozilla/5.0 (Linux; Android 11; moto e20 Build/RONS31.267-94-14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.178 Mobile Safari/537.3",
  "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Mobile Safari/537.3",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Mobile/15E148 Safari/604.",
  "Mozilla/5.0 (Linux; Android 12; SM-S906N Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/80.0.3987.119 Mobile Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/37.0.2062.94 Chrome/37.0.2062.94 Safari/537.36",
  "Mozilla/5.0 (Linux; Android 10; SM-G996U Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Mobile Safari/537.36",
  "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.85 Safari/537.36",
  "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko",
  "Mozilla/5.0 (Linux; Android 10; SM-G980F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/78.0.3904.96 Mobile Safari/537.36",
  "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:40.0) Gecko/20100101 Firefox/40.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5) AppleWebKit/600.8.9 (KHTML, like Gecko) Version/8.0.8 Safari/600.8.9",
  "Mozilla/5.0 (iPad; CPU OS 8_4_1 like Mac OS X) AppleWebKit/600.1.4 (KHTML, like Gecko) Version/8.0 Mobile/12H321 Safari/600.1.4",
  "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.85 Safari/537.36",
  "Opera/9.80 (Windows NT 6.0) Presto/2.12.388 Version/12.14",
  "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.85 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/42.0.2311.135 Safari/537.36 Edge/12.10240",
  "Mozilla/5.0 (Windows NT 6.3; WOW64; rv:40.0) Gecko/20100101 Firefox/40.0",
  "Mozilla/5.0 (iPhone14,6; U; CPU iPhone OS 15_4 like Mac OS X) AppleWebKit/602.1.50 (KHTML, like Gecko) Version/10.0 Mobile/19E241 Safari/602.1",
  "Mozilla/5.0 (Windows NT 6.3; WOW64; Trident/7.0; rv:11.0) like Gecko",
  "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.85 Safari/537.36",
  "Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko",
  "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:40.0) Gecko/20100101 Firefox/40.0",
  "Mozilla/5.0 (Linux; Android 9; SM-G973U Build/PPR1.180610.011) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.100 Mobile Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_4) AppleWebKit/600.7.12 (KHTML, like Gecko) Version/8.0.7 Safari/600.7.12",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.85 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.10; rv:40.0) Gecko/20100101 Firefox/40.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_5) AppleWebKit/600.8.9 (KHTML, like Gecko) Version/7.1.8 Safari/537.85.17",
  "Mozilla/5.0 (iPad; CPU OS 8_4 like Mac OS X) AppleWebKit/600.1.4 (KHTML, like Gecko) Version/8.0 Mobile/12H143 Safari/600.1.4",
  "Mozilla/5.0 (Linux; Android 8.0.0; SM-G960F Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/62.0.3202.84 Mobile Safari/537.36",
  "Mozilla/5.0 (iPad; CPU OS 8_3 like Mac OS X) AppleWebKit/600.1.4 (KHTML, like Gecko) Version/8.0 Mobile/12F69 Safari/600.1.4",
  "Mozilla/5.0 (Windows NT 6.1; rv:40.0) Gecko/20100101 Firefox/40.0",
  "Mozilla/5.0 (Linux; Android 7.0; SM-G930VC Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/58.0.3029.83 Mobile Safari/537.36",
  "Mozilla/5.0 (compatible; MSIE 10.0; Windows NT 6.1; WOW64; Trident/6.0)",
  "Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; WOW64; Trident/5.0)",
  "Mozilla/5.0 (Windows NT 6.3; WOW64; Trident/7.0; Touch; rv:11.0) like Gecko",
  "Mozilla/5.0 (Windows NT 5.1; rv:40.0) Gecko/20100101 Firefox/40.0",
  "Mozilla/5.0 (Linux; Android 6.0.1; SM-G935S Build/MMB29K; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/55.0.2883.91 Mobile Safari/537.36",
  "Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.85 Safari/537.36",
  "Mozilla/5.0 (Linux; Android 5.1.1; SM-G928X Build/LMY47X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/47.0.2526.83 Mobile Safari/537.36"
]

# 创建全局连接池
_conn_pool = {}

def get_db_connection():
    """获取数据库连接，使用连接池避免频繁打开关闭连接"""
    # 使用线程ID作为键，确保线程安全
    thread_id = os.getpid()
    if thread_id not in _conn_pool:
        # 确保缓存目录存在
        os.makedirs(os.path.dirname(CACHE_DB_FILE), exist_ok=True)
        conn = sqlite3.connect(CACHE_DB_FILE, check_same_thread=False)
        # 启用外键约束
        conn.execute("PRAGMA foreign_keys = ON")
        # 启用WAL模式，提高写入性能
        conn.execute("PRAGMA journal_mode = WAL")
        # 设置同步模式为NORMAL，平衡性能和安全性
        conn.execute("PRAGMA synchronous = NORMAL")
        # 设置缓存大小，提高查询性能
        conn.execute("PRAGMA cache_size = -4000")  # 约4MB缓存
        _conn_pool[thread_id] = conn
    return _conn_pool[thread_id]

def sanitize_table_name(keyword):
    """将关键词转换为有效的SQL表名"""
    # 移除URL编码
    decoded = unquote(keyword)
    # 替换非法字符
    sanitized = re.sub(r'[^\w]', '_', decoded)
    # 确保不以数字开头
    if sanitized[0].isdigit():
        sanitized = 'k_' + sanitized
    return 'papers_' + sanitized

def init_cache_db():
    """初始化SQLite缓存数据库和表结构"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 创建关键词主表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keywords (
            keyword TEXT PRIMARY KEY,
            table_name TEXT,
            timestamp INTEGER,
            rsstext TEXT
        )
    ''')
    
    # 创建索引，提高查询性能
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_keywords_table_name ON keywords(table_name)')
    
    conn.commit()

def create_paper_table(conn, table_name):
    """为指定关键词创建论文表"""
    cursor = conn.cursor()
    
    # 检查表是否存在
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
    table_exists = cursor.fetchone() is not None
    
    if not table_exists:
        # 创建表，并设置自增ID
        cursor.execute(f'''
            CREATE TABLE {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT UNIQUE,
                authors TEXT,
                year INTEGER,
                publication_date TEXT,
                url TEXT,
                guid TEXT,
                venue TEXT,
                volume TEXT,
                number TEXT,
                doi TEXT,
                ee TEXT,
                dc_language TEXT,
                dc_type TEXT,
                additional_data TEXT
            )
        ''')
        
        # 创建索引，提高查询性能
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_year ON {table_name}(year DESC)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_pub_date ON {table_name}(publication_date DESC)')
        
        # 设置SQLite序列，确保即使删除记录后ID也继续递增
        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table_name}'")
        cursor.execute(f"INSERT INTO sqlite_sequence (name, seq) VALUES ('{table_name}', 0)")
    
    conn.commit()

def load_from_cache_db(keyword):
    """从SQLite缓存加载结果，如果存在且未过期"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查询这个关键词的表信息
    cursor.execute("SELECT table_name, timestamp, rsstext FROM keywords WHERE keyword = ?", (keyword,))
    row = cursor.fetchone()
    
    if not row:
        return None
        
    table_name, timestamp_int, rsstext = row
    timestamp = datetime.datetime.fromtimestamp(timestamp_int)
    now = datetime.datetime.now()
    
    # 检查缓存是否过期
    if (now - timestamp).total_seconds() >= CACHE_EXPIRATION_HOURS * 3600:
        return None
    
    # 如果有缓存的RSS文本，直接返回
    if rsstext:
        print(f"DEBUG: 直接返回缓存的RSS文本，关键词: {keyword}")
        return rsstext
    
    # 兼容旧数据：如果没有缓存的RSS文本，从表中获取数据并生成
    # 修改排序逻辑: 年份降序，发布日期降序，ID升序
    cursor.execute(f"SELECT * FROM {table_name} ORDER BY year DESC, publication_date DESC, id ASC")
    papers = cursor.fetchall()
    
    if not papers:
        return None
    
    # 生成RSS数据
    columns = [desc[0] for desc in cursor.description]
    papers_data = [dict(zip(columns, paper)) for paper in papers]
    
    # 使用获取的论文数据生成RSS
    rss_data = generate_rss_from_papers(papers_data)
    
    # 更新keywords表中的rsstext字段
    cursor.execute('''
        UPDATE keywords SET rsstext = ? WHERE keyword = ?
    ''', (rss_data, keyword))
    conn.commit()
    
    return rss_data

def save_to_cache_db(keyword, papers_data):
    """将论文数据保存到SQLite缓存"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 生成表名并创建表
    table_name = sanitize_table_name(keyword)
    create_paper_table(conn, table_name)
    
    # 获取当前时间戳，用于记录插入时间
    now_timestamp = int(datetime.datetime.now().timestamp())
    
    # 使用事务处理批量操作，提高性能
    conn.execute("BEGIN TRANSACTION")
    
    try:
        # 获取现有记录数量和标题，只查询一次数据库
        try:
            # 尝试使用GROUP_CONCAT (某些SQLite版本可能不支持)
            cursor.execute(f"SELECT COUNT(*), GROUP_CONCAT(title, '|') FROM {table_name}")
            result = cursor.fetchone()
            current_count = result[0] if result[0] is not None else 0
            existing_titles = set(result[1].split('|')) if result[1] and result[1] is not None else set()
        except sqlite3.OperationalError:
            # 如果GROUP_CONCAT不可用，回退到传统方法
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            current_count = cursor.fetchone()[0]
            existing_titles = set()
            if current_count > 0:
                cursor.execute(f"SELECT title FROM {table_name}")
                for row in cursor.fetchall():
                    if row[0]:  # 确保标题不为None
                        existing_titles.add(row[0])
        
        # 过滤出新论文，避免重复插入
        new_papers = [paper for paper in papers_data if paper['title'] not in existing_titles]
        
        print(f"DEBUG: 过滤前论文数: {len(papers_data)}, 过滤后新论文数: {len(new_papers)}")
        
        # 如果没有新论文，直接返回
        if not new_papers and current_count > 0:
            # 生成RSS数据 - 修改排序逻辑: 年份降序，发布日期降序，ID升序
            cursor.execute(f"SELECT * FROM {table_name} ORDER BY year DESC, publication_date DESC, id ASC")
            papers = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            existing_papers_data = [dict(zip(columns, paper)) for paper in papers]
            
            rss_data = generate_rss_from_papers(existing_papers_data)
            
            # 更新关键词表的时间戳和RSS文本
            cursor.execute('''
                INSERT OR REPLACE INTO keywords (keyword, table_name, timestamp, rsstext)
                VALUES (?, ?, ?, ?)
            ''', (keyword, table_name, now_timestamp, rss_data))
            conn.commit()
            return
        
        # 如果不需要清空现有数据，就保留最新的记录
        if current_count > 0:
            # 如果现有记录数加上新记录数超过上限，则删除旧记录
            if current_count + len(new_papers) > MAX_TABLE_RECORDS:
                # 计算需要保留的旧记录数
                keep_count = MAX_TABLE_RECORDS - len(new_papers)
                if keep_count < 0:
                    keep_count = 0
                
                # 修改排序逻辑：年份降序，发布日期降序，ID升序，保留最新的记录
                cursor.execute(f'''
                    DELETE FROM {table_name} 
                    WHERE id NOT IN (
                        SELECT id FROM {table_name} 
                        ORDER BY year DESC, publication_date DESC, id ASC
                        LIMIT {keep_count}
                    )
                ''')
                print(f"DEBUG: 已从表 {table_name} 删除旧记录，保留 {keep_count} 条")
        else:
            # 如果表是空的，直接清空
            cursor.execute(f"DELETE FROM {table_name}")
        
        # 批量插入新的论文数据，减少数据库操作次数
        if new_papers:
            # 预处理数据，移除id字段
            for paper in new_papers:
                if 'id' in paper:
                    del paper['id']
            
            # 准备批量插入
            first_paper = new_papers[0]
            columns = list(first_paper.keys())
            placeholders = ', '.join(['?'] * len(columns))
            columns_str = ', '.join(columns)
            
            # 构建批量插入语句
            batch_data = []
            for paper in new_papers:
                try:
                    # 确保所有论文都有相同的字段
                    row_values = [paper.get(col, '') for col in columns]
                    batch_data.append(row_values)
                except Exception as e:
                    print(f"DEBUG: 准备插入数据时出错: {str(e)}")
            
            # 执行批量插入，使用executemany
            try:
                query = f"INSERT OR IGNORE INTO {table_name} ({columns_str}) VALUES ({placeholders})"
                cursor.executemany(query, batch_data)
                print(f"DEBUG: 批量插入 {cursor.rowcount} 条记录")
            except sqlite3.IntegrityError as e:
                print(f"DEBUG: 批量插入时出现完整性错误: {str(e)}")
                # 如果批量插入失败，回退到单条插入
                for paper in new_papers:
                    try:
                        placeholders = ', '.join(['?'] * len(paper))
                        columns = ', '.join(paper.keys())
                        query = f"INSERT OR IGNORE INTO {table_name} ({columns}) VALUES ({placeholders})"
                        cursor.execute(query, list(paper.values()))
                    except sqlite3.IntegrityError:
                        # 忽略重复记录
                        pass
        
        # 确保表中记录数不超过最大限制
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        final_count = cursor.fetchone()[0]
        if final_count > MAX_TABLE_RECORDS:
            # 删除多余的旧记录，修改排序逻辑
            cursor.execute(f'''
                DELETE FROM {table_name} 
                WHERE id NOT IN (
                    SELECT id FROM {table_name} 
                    ORDER BY year DESC, publication_date DESC, id ASC
                    LIMIT {MAX_TABLE_RECORDS}
                )
            ''')
            print(f"DEBUG: 最终清理，表 {table_name} 记录数限制为 {MAX_TABLE_RECORDS}")
        
        # 获取最新的数据生成RSS - 修改排序逻辑
        cursor.execute(f"SELECT * FROM {table_name} ORDER BY year DESC, publication_date DESC, id ASC")
        papers = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        updated_papers_data = [dict(zip(columns, paper)) for paper in papers]
        
        rss_data = generate_rss_from_papers(updated_papers_data)
        
        # 更新关键词表，包含RSS文本
        cursor.execute('''
            INSERT OR REPLACE INTO keywords (keyword, table_name, timestamp, rsstext)
            VALUES (?, ?, ?, ?)
        ''', (keyword, table_name, now_timestamp, rss_data))
        
        # 提交事务
        conn.commit()
        
    except Exception as e:
        # 如果出现任何错误，回滚事务
        conn.rollback()
        print(f"ERROR: 保存缓存时出错: {str(e)}")
        raise

def get_json_from_dblp(keyword: str, nb_entries: int):
    BASE_URL = "https://dblp.org/search/publ/api"

    # 判断 keyword 是否已经是 URL 编码形式
    decoded_keyword = unquote(keyword)  # 尝试解码
    if decoded_keyword != keyword:  # 如果解码后不同，说明是编码过的字符串
        final_keyword = decoded_keyword  # 使用解码后的字符串
    else:
        final_keyword = keyword  # 使用原始字符串

    parameters = {
        "q": final_keyword,
        "h": FETCH_ENTRIES,  # 使用FETCH_ENTRIES来获取更多结果
        "format": "json",
    }

    # 随机选择一个User-Agent
    random_ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": random_ua
    }
    
    # 添加重试机制
    max_retries = 3
    retry_delay = 1  # 初始延迟1秒
    
    for attempt in range(max_retries):
        try:
            res = requests.get(BASE_URL, headers=headers, params=parameters, timeout=10)
            if res.status_code == 200:
                try:
                    return json.loads(res.content)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Error {e} when parsing the JSON entry from DBLP")
            elif res.status_code == 429:  # Too Many Requests
                # 如果被限流，等待更长时间再重试
                if attempt < max_retries - 1:
                    sleep_time = retry_delay * (2 ** attempt)  # 指数退避
                    print(f"Rate limited by DBLP. Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                    continue
            else:
                raise ValueError(f"Error {res.status_code} when fetching DBLP for keyword {keyword}")
        except requests.RequestException as e:
            if attempt < max_retries - 1:
                sleep_time = retry_delay * (2 ** attempt)
                print(f"Network error: {str(e)}. Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                raise ValueError(f"Network error after {max_retries} attempts: {str(e)}")
    
    raise ValueError(f"Failed to fetch data from DBLP after {max_retries} attempts")

def sort_hits_by_year_volume_and_number(hits):
    def parse_int(value, default=0):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    # 使用稳定排序方法
    hits.sort(key=lambda x: parse_int(x['info'].get('number', 0)), reverse=True)
    hits.sort(key=lambda x: parse_int(x['info'].get('volume', 0)), reverse=True)
    hits.sort(key=lambda x: parse_int(x['info'].get('year', 0)), reverse=True)
    return hits

def process_papers_from_json(json_data):
    """从DBLP JSON数据处理论文信息，返回结构化的论文数据列表"""
    # 确保值是字符串类型的辅助函数
    def ensure_string(value):
        if value is None:
            return ""
        elif isinstance(value, list):
            return ", ".join(str(x) for x in value)
        else:
            return str(value)

    # 获取当前时间作为publication_date
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 获取返回数据中的 'hit' 列表, 并确保其为列表
    raw_hits = json_data['result']['hits'].get('hit', [])
    if isinstance(raw_hits, dict):
        hits = [raw_hits]
    else:
        hits = raw_hits
    
    # 按 year, volume 和 number 排序
    sorted_hits = sort_hits_by_year_volume_and_number(hits)
    
    # 只取前 NB_ENTRIES 个条目
    sorted_hits = sorted_hits[:NB_ENTRIES]
    
    papers_data = []
    
    for entry in sorted_hits:
        paper = {}
        
        # 提取基本信息
        paper['title'] = ensure_string(entry['info'].get('title'))
        
        # 处理作者信息
        authors_list = entry['info'].get('authors', {}).get('author', [])
        if isinstance(authors_list, list):
            paper['authors'] = ", ".join(ensure_string(author_info.get('text', '')) for author_info in authors_list)
        elif isinstance(authors_list, dict):
            paper['authors'] = ensure_string(authors_list.get('text', ''))
        else:
            paper['authors'] = ""
        
        # 年份和日期
        year = ensure_string(entry['info'].get('year', "2000"))
        paper['year'] = int(year)
        paper['publication_date'] = current_time  # 使用完整的日期时间格式
        
        # URL和标识符
        paper['url'] = ensure_string(entry['info'].get('url'))
        paper['guid'] = ensure_string(entry['info'].get('key', entry['info'].get('url')))
        
        # 期刊/会议信息
        paper['venue'] = ensure_string(entry['info'].get('venue', ''))
        paper['volume'] = ensure_string(entry['info'].get('volume', ''))
        paper['number'] = ensure_string(entry['info'].get('number', ''))
        
        # DOI和电子版链接
        paper['doi'] = ensure_string(entry['info'].get('doi', ''))
        
        ee_value = entry['info'].get('ee', '')
        if isinstance(ee_value, list) and ee_value:
            paper['ee'] = ensure_string(ee_value[0])
        else:
            paper['ee'] = ensure_string(ee_value)
        
        # 其他元数据
        paper['dc_language'] = "EN"
        paper['dc_type'] = "text"
        
        papers_data.append(paper)
    
    return papers_data

def generate_rss_from_papers(papers_data):
    """从论文数据列表生成RSS feed"""
    # 如果数据已经排序，可以跳过排序步骤
    if not papers_data:
        # 创建空的RSS feed
        rss = ET.Element('rss', version="2.0")
        channel = ET.SubElement(rss, 'channel')
        title = ET.SubElement(channel, 'title')
        title.text = "DBLP"
        link = ET.SubElement(channel, 'link')
        link.text = "https://dblp.org/"
        description = ET.SubElement(channel, 'description')
        description.text = "No results found"
        ET.indent(rss)
        return ET.tostring(rss, method='xml', encoding="unicode")
    
    # 创建 RSS 根元素
    rss = ET.Element('rss', 
                     attrib={
                         "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
                         "xmlns:wfw": "http://wellformedweb.org/CommentAPI/",
                         "xmlns:dc": "http://purl.org/dc/elements/1.1/",
                         "xmlns:atom": "http://www.w3.org/2005/Atom",
                         "xmlns:sy": "http://purl.org/rss/1.0/modules/syndication/",
                         "xmlns:slash": "http://purl.org/rss/1.0/modules/slash/",
                         "xmlns:prism": "http://prismstandard.org/namespaces/basic/2.0/",
                         "xmlns:rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                         "xmlns:media": "http://search.yahoo.com/mrss/",
                         "version": "2.0"
                     })

    # 创建频道元素
    channel = ET.SubElement(rss, 'channel')

    # 添加频道相关信息
    title = ET.SubElement(channel, 'title')
    title.text = "DBLP"
    link = ET.SubElement(channel, 'link')
    link.text = "https://dblp.org/"
    description = ET.SubElement(channel, 'description')
    description.text = "Custom DBLP extraction from XML"
    language = ET.SubElement(channel, 'language')
    language.text = 'en'
    
    # 添加频道更新时间
    lastBuildDate = ET.SubElement(channel, 'lastBuildDate')
    lastBuildDate.text = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")

    # 为每篇论文创建RSS项
    for paper in papers_data:
        item = ET.SubElement(channel, 'item')
        
        # 添加rdf:about属性
        if paper.get('url'):
            item.set('rdf:about', paper['url'])
            
        # 添加标题
        title = ET.SubElement(item, 'title')
        title.text = paper.get('title', '')
        
        # 添加dc:title
        dc_title = ET.SubElement(item, 'dc:title')
        dc_title.text = paper.get('title', '')

        # 处理作者信息 - 每位作者单独一个dc:creator标签
        if paper.get('authors'):
            for author_name in paper['authors'].split(', '):
                if author_name.strip():
                    author = ET.SubElement(item, 'dc:creator')
                    author.text = author_name.strip()

        # 添加出版日期 - 使用当前时间作为pubDate
        date = ET.SubElement(item, 'pubDate')
        
        # 将数据库格式的时间转换为RSS格式
        rfc822_date = None
        try:
            # 尝试解析完整的日期时间格式 YYYY-MM-DD HH:MM:SS
            pub_datetime = datetime.datetime.strptime(paper.get('publication_date', ''), "%Y-%m-%d %H:%M:%S")
            rfc822_date = pub_datetime.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except ValueError:
            try:
                # 尝试解析YYYY-MM-DD格式
                pub_datetime = datetime.datetime.strptime(paper.get('publication_date', ''), "%Y-%m-%d")
                # 添加时间部分，确保每次生成相同的时间
                pub_datetime = pub_datetime.replace(hour=12, minute=0, second=0)
                rfc822_date = pub_datetime.strftime("%a, %d %b %Y %H:%M:%S +0000")
            except ValueError:
                # 如果转换失败，使用论文年份
                year = str(paper.get('year', 2000))
                d = datetime.datetime(int(year), 1, 1, 12, 0, 0)
                rfc822_date = d.strftime("%a, %d %b %Y %H:%M:%S +0000")
        
        date.text = rfc822_date
        
        # 添加dc:date - 使用ISO 8601格式 (YYYY-MM-DD)
        dc_date = ET.SubElement(item, 'dc:date')
        iso_date = None
        try:
            pub_datetime = datetime.datetime.strptime(paper.get('publication_date', ''), "%Y-%m-%d %H:%M:%S")
            iso_date = pub_datetime.strftime("%Y-%m-%d")
        except ValueError:
            try:
                pub_datetime = datetime.datetime.strptime(paper.get('publication_date', ''), "%Y-%m-%d")
                iso_date = pub_datetime.strftime("%Y-%m-%d")
            except ValueError:
                iso_date = f"{paper.get('year', 2000)}-01-01"
        
        dc_date.text = iso_date
        
        # 添加prism:publicationDate - 使用相同的ISO格式
        prism_pubdate = ET.SubElement(item, 'prism:publicationDate')
        prism_pubdate.text = iso_date

        # 添加链接
        link = ET.SubElement(item, 'link')
        link.text = paper.get('url', '')

        # 添加唯一标识符（guid）
        guid = ET.SubElement(item, 'guid')
        guid_value = paper.get('guid', paper.get('url', ''))
        guid.text = guid_value
        guid.set('rdf:resource', guid_value)
        # 设置为永久链接
        guid.set('isPermaLink', 'true')
        
        # 添加description标签，包含摘要信息
        description = ET.SubElement(item, 'description')
        description_text = f"<p>Year: {paper.get('year', '')}</p>"
        if paper.get('venue'):
            description_text += f"<p>Venue: {paper.get('venue', '')}</p>"
        if paper.get('authors'):
            description_text += f"<p>Authors: {paper.get('authors', '')}</p>"
        description.text = description_text
        
        # 添加dc:language
        dc_language = ET.SubElement(item, 'dc:language')
        dc_language.text = paper.get('dc_language', 'EN')

        # 添加期刊相关信息
        if paper.get('venue'):
            # prism:publicationName
            prism_pubname = ET.SubElement(item, 'prism:publicationName')
            prism_pubname.text = paper['venue']
            
            # dc:source
            dc_source = ET.SubElement(item, 'dc:source')
            dc_source.text = paper['venue']
            
        if paper.get('volume'):
            prism_volume = ET.SubElement(item, 'prism:volume')
            prism_volume.text = paper['volume']
            
        if paper.get('number'):
            prism_number = ET.SubElement(item, 'prism:number')
            prism_number.text = paper['number']
            
        if paper.get('doi'):
            dc_identifier = ET.SubElement(item, 'dc:identifier')
            dc_identifier.text = f"doi:{paper['doi']}"
            
        if paper.get('ee'):
            dc_format = ET.SubElement(item, 'dc:format')
            dc_format.text = "text/html"
            
            urls = ET.SubElement(item, 'dc:relation')
            urls.text = paper['ee']
            
        # 添加dc:type
        dc_type = ET.SubElement(item, 'dc:type')
        dc_type.text = paper.get('dc_type', 'text')

    # 格式化 RSS 输出
    ET.indent(rss)
    return ET.tostring(rss, method='xml', encoding="unicode")

def generate_rss_feed(json_data):
    """处理JSON数据并生成RSS feed"""
    papers_data = process_papers_from_json(json_data)
    return generate_rss_from_papers(papers_data), papers_data

def dblp_rss(keyword):
    # 记录开始时间，用于性能分析
    start_time = time.time()
    
    # 检查缓存
    cached_result = load_from_cache_db(keyword)
    if cached_result:
        print(f"DEBUG: 缓存命中，耗时 {time.time() - start_time:.3f} 秒")
        return cached_result

    # 缓存失效或不存在
    print(f"DEBUG: 从DBLP获取 {keyword} 的新数据。")
    json_data = get_json_from_dblp(keyword, NB_ENTRIES)

    # 如果 DBLP 没有返回结果，则生成一个空的 RSS feed 以避免崩溃
    if not json_data['result']['hits'].get('hit'):
        json_data = {'result': {'hits': {'hit': []}}}
    
    result, papers_data = generate_rss_feed(json_data)
    
    # 保存到缓存
    save_to_cache_db(keyword, papers_data)
    
    # 返回生成的RSS
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT rsstext FROM keywords WHERE keyword = ?", (keyword,))
    row = cursor.fetchone()
    
    final_result = row[0] if row and row[0] else result
    print(f"DEBUG: 完成处理，总耗时 {time.time() - start_time:.3f} 秒")
    return final_result

if __name__ == '__main__':
    init_cache_db()
    # dblp_rss("stream:streams/journals/tdsc:")
    # dblp_rss("stream%3Astreams%2Fjournals%2Ftdsc%3A")
    dblp_rss("stream%3Astreams%2Fconf%2Feurocrypt%3A")
