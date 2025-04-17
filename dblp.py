import requests
import json
import datetime
from xml.etree import ElementTree as ET
from urllib.parse import quote, unquote
import os
import pickle

# How many articles must be pulled
NB_ENTRIES = 300
# Cache expiration time in hours
CACHE_EXPIRATION_HOURS = 12

CACHE_FILE = "cache/dblp_cache.pkl"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "rb") as f:
            return pickle.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(cache, f)

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
        "h": nb_entries,
        "format": "json",
        "c": 0
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }

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

    # 创建 RSS 根元素
    rss = ET.Element('rss',
                     attrib={
                         "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
                         "xmlns:wfw": "http://wellformedweb.org/CommentAPI/",
                         "xmlns:dc": "http://purl.org/dc/elements/1.1/",
                         "xmlns:atom": "http://www.w3.org/2005/Atom",
                         "xmlns:sy": "http://purl.org/rss/1.0/modules/syndication/",
                         "xmlns:slash": "http://purl.org/rss/1.0/modules/slash/",
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
    
    # 获取返回数据中的 'hit' 列表
    hits = json_data['result']['hits'].get('hit', [])
    # print(f"DEBUG: Parsed hits: {hits}")  # 调试信息，输出 hits 内容

    # 按 year, volume 和 number 排序
    sorted_hits = sort_hits_by_year_volume_and_number(hits)

    # 遍历每个条目并生成 RSS item
    for entry in sorted_hits:
        # print(f"DEBUG: Processing entry: {entry}")  # 调试信息，输出当前条目内容
        item = ET.SubElement(channel, 'item')
        title = ET.SubElement(item, 'title')
        title.text = entry['info']['title']

        # 处理作者信息
        author = ET.SubElement(item, 'dc:creator')
        authors_list = entry['info'].get('authors', {}).get('author', "NULL")
        if isinstance(authors_list, list):
            author.text = ' | '.join(a['text'] for a in authors_list)
        elif isinstance(authors_list, dict):
            author.text = authors_list['text']

        # 添加出版日期
        date = ET.SubElement(item, 'pubDate')
        year = entry['info'].get('year', "2000")  # 默认年份为 2000
        d = datetime.datetime(int(year), 1, 1)
        date.text = d.strftime("%a, %d %b %Y %H:%M:%S +0000")

        # 添加链接
        link = ET.SubElement(item, 'link')
        link.text = entry['info']['url']
        
        # 添加唯一标识符（guid）
        guid = ET.SubElement(item, 'guid')
        guid.set('isPermaLink', 'false')
        # 使用键值作为唯一标识符
        if 'key' in entry['info']:
            guid.text = entry['info']['key']
        else:
            # 如果没有key，使用URL作为唯一标识符
            guid.text = entry['info']['url']

        # 添加描述
        description = ET.SubElement(item, 'description')
        description.text = '\n'.join(
            f"{key}: {val}" for key, val in entry['info'].items() if key in ['venue', 'year', 'ee'])

    # 格式化 RSS 输出
    ET.indent(rss)
    return ET.tostring(rss, method='xml', encoding="unicode")

def dblp_rss(keyword):
    cache = load_cache()
    now = datetime.datetime.now()

    # 检查缓存
    if keyword in cache:
        cached_data, timestamp = cache[keyword]
        if (now - timestamp).total_seconds() < CACHE_EXPIRATION_HOURS * 3600:
            print("DEBUG: Returning cached result.")
            return cached_data

    # 缓存失效或不存在
    print("DEBUG: Fetching new data from DBLP.")
    result = generate_rss_feed(get_json_from_dblp(keyword, NB_ENTRIES))
    cache[keyword] = (result, now)
    save_cache(cache)
    return result

if __name__ == '__main__':
    dblp_rss("stream:streams/journals/tdsc:")
    dblp_rss("stream%3Astreams%2Fjournals%2Ftdsc%3A")
