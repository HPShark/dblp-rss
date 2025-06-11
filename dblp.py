import requests
import json
import datetime
import random
from xml.etree import ElementTree as ET
from urllib.parse import quote, unquote
import os
import sqlite3

# How many articles must be pulled
NB_ENTRIES = 300
# How many articles to fetch from DBLP API
FETCH_ENTRIES = 1000
# Cache expiration time in hours
CACHE_EXPIRATION_HOURS = 12

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

def init_cache_db():
    """Initializes the SQLite cache database and table."""
    # Ensure the cache directory exists
    os.makedirs(os.path.dirname(CACHE_DB_FILE), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache (
            keyword TEXT PRIMARY KEY,
            timestamp INTEGER,
            rss_data TEXT
        )
    ''')
    conn.commit()
    conn.close()

def load_from_cache_db(keyword: str):
    """Loads a result from the SQLite cache if it exists and is not expired."""
    conn = sqlite3.connect(CACHE_DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, rss_data FROM cache WHERE keyword = ?", (keyword,))
    row = cursor.fetchone()
    conn.close()

    if row:
        timestamp_int, rss_data = row
        timestamp = datetime.datetime.fromtimestamp(timestamp_int)
        now = datetime.datetime.now()
        if (now - timestamp).total_seconds() < CACHE_EXPIRATION_HOURS * 3600:
            print("DEBUG: Returning cached result from DB.")
            return rss_data
    return None

def save_to_cache_db(keyword: str, rss_data: str):
    """Saves a result to the SQLite cache."""
    conn = sqlite3.connect(CACHE_DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    now_timestamp = int(datetime.datetime.now().timestamp())
    cursor.execute('''
        INSERT OR REPLACE INTO cache (keyword, timestamp, rss_data)
        VALUES (?, ?, ?)
    ''', (keyword, now_timestamp, rss_data))
    conn.commit()
    conn.close()


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
    
    # print(f"DEBUG: Using User-Agent: {random_ua}")

    res = requests.get(BASE_URL, headers=headers, params=parameters)
    # print(f"DEBUG: URL called: {res.url}")  # 输出实际调用的 URL
    if res.status_code == 200:
        try:
            return json.loads(res.content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Error {e} when parsing the JSON entry from DBLP")
    else:
        raise ValueError(f"Error {res.status_code} when fetching DBLP for keyword {keyword}")


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


def generate_rss_feed(json_data):
    """Formats the json result from DBLP to a valid RSS file."""
    
    # 添加辅助函数确保值是字符串类型
    def ensure_string(value):
        """确保值是字符串类型"""
        if value is None:
            return ""
        elif isinstance(value, list):
            return ", ".join(str(x) for x in value)
        else:
            return str(value)

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

    # 遍历每个条目并生成 RSS item
    for entry in sorted_hits:
        item = ET.SubElement(channel, 'item')
        
        # 添加rdf:about属性
        if 'url' in entry['info']:
            item.set('rdf:about', ensure_string(entry['info']['url']))
            
        # 添加标题
        title = ET.SubElement(item, 'title')
        title.text = ensure_string(entry['info'].get('title'))
        
        # 添加dc:title
        dc_title = ET.SubElement(item, 'dc:title')
        dc_title.text = ensure_string(entry['info'].get('title'))

        # 处理作者信息 - 每位作者单独一个dc:creator标签
        authors_list = entry['info'].get('authors', {}).get('author', [])
        if isinstance(authors_list, list):
            for author_info in authors_list:
                author = ET.SubElement(item, 'dc:creator')
                author.text = ensure_string(author_info.get('text', ''))
        elif isinstance(authors_list, dict):
            author = ET.SubElement(item, 'dc:creator')
            author.text = ensure_string(authors_list.get('text', ''))

        # 添加出版日期
        date = ET.SubElement(item, 'pubDate')
        year = ensure_string(entry['info'].get('year', "2000"))  # 默认年份为 2000
        d = datetime.datetime(int(year), 1, 1)
        date.text = d.strftime("%a, %d %b %Y %H:%M:%S +0000")
        
        # 添加dc:date
        dc_date = ET.SubElement(item, 'dc:date')
        dc_date.text = f"{year}-01-01"
        
        # 添加prism:publicationDate
        prism_pubdate = ET.SubElement(item, 'prism:publicationDate')
        prism_pubdate.text = f"{year}-01-01"

        # 添加链接
        link = ET.SubElement(item, 'link')
        link.text = ensure_string(entry['info'].get('url'))

        # 添加唯一标识符（guid）
        guid = ET.SubElement(item, 'guid')
        if 'key' in entry['info']:
            guid_value = ensure_string(entry['info']['key'])
        else:
            guid_value = ensure_string(entry['info'].get('url'))
            
        guid.text = guid_value
        guid.set('rdf:resource', guid_value)
        
        # 添加空的description标签
        description = ET.SubElement(item, 'description')
        
        # 添加dc:language
        dc_language = ET.SubElement(item, 'dc:language')
        dc_language.text = "EN"

        # 添加期刊相关信息
        if 'venue' in entry['info']:
            # prism:publicationName
            prism_pubname = ET.SubElement(item, 'prism:publicationName')
            prism_pubname.text = ensure_string(entry['info']['venue'])
            
            # dc:source (应该包含issn，但dblp没有这个信息，用venue代替)
            dc_source = ET.SubElement(item, 'dc:source')
            dc_source.text = ensure_string(entry['info']['venue'])
            
        if 'volume' in entry['info']:
            prism_volume = ET.SubElement(item, 'prism:volume')
            prism_volume.text = ensure_string(entry['info']['volume'])
            
        if 'number' in entry['info']:
            prism_number = ET.SubElement(item, 'prism:number')
            prism_number.text = ensure_string(entry['info']['number'])
            
        if 'doi' in entry['info']:
            dc_identifier = ET.SubElement(item, 'dc:identifier')
            dc_identifier.text = f"doi:{ensure_string(entry['info']['doi'])}"
            
        if 'ee' in entry['info']:
            dc_format = ET.SubElement(item, 'dc:format')
            dc_format.text = "text/html"
            
            # 处理ee字段，它可能是列表或字符串
            ee_value = entry['info']['ee']
            if isinstance(ee_value, list) and ee_value:
                # 如果是列表，取第一个值
                urls = ET.SubElement(item, 'dc:relation')
                urls.text = ensure_string(ee_value[0])
            else:
                urls = ET.SubElement(item, 'dc:relation')
                urls.text = ensure_string(ee_value)
            
        # 添加dc:type
        dc_type = ET.SubElement(item, 'dc:type')
        dc_type.text = "text"

    # 格式化 RSS 输出
    ET.indent(rss)
    return ET.tostring(rss, method='xml', encoding="unicode")


def dblp_rss(keyword):
    # 检查缓存
    cached_result = load_from_cache_db(keyword)
    if cached_result:
        return cached_result

    # 缓存失效或不存在
    print(f"DEBUG: Fetching new data for {keyword} from DBLP.")
    json_data = get_json_from_dblp(keyword, NB_ENTRIES)

    # 如果 DBLP 没有返回结果，则生成一个空的 RSS feed 以避免崩溃
    if not json_data['result']['hits'].get('hit'):
        json_data = {'result': {'hits': {'hit': []}}}
    
    result = generate_rss_feed(json_data)
    
    # 保存到缓存
    save_to_cache_db(keyword, result)
    
    return result


if __name__ == '__main__':
    init_cache_db()
    # dblp_rss("stream:streams/journals/tdsc:")
    # dblp_rss("stream%3Astreams%2Fjournals%2Ftdsc%3A")
    dblp_rss("stream%3Astreams%2Fconf%2Feurocrypt%3A")
