import requests
import json
import datetime
from xml.etree import ElementTree as ET
from urllib.parse import quote, unquote
import os
import pickle

# How many articles must be pulled
NB_ENTRIES = 300
# How many articles to fetch from DBLP API
FETCH_ENTRIES = 1000
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
        "h": FETCH_ENTRIES,  # 使用FETCH_ENTRIES来获取更多结果
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

    # 获取返回数据中的 'hit' 列表
    hits = json_data['result']['hits'].get('hit', [])
    
    # 按 year, volume 和 number 排序
    sorted_hits = sort_hits_by_year_volume_and_number(hits)
    
    # 只取前 NB_ENTRIES 个条目
    sorted_hits = sorted_hits[:NB_ENTRIES]

    # 遍历每个条目并生成 RSS item
    for entry in sorted_hits:
        item = ET.SubElement(channel, 'item')
        
        # 添加rdf:about属性
        if 'url' in entry['info']:
            item.set('rdf:about', entry['info']['url'])
            
        # 添加标题
        title = ET.SubElement(item, 'title')
        title.text = entry['info']['title']
        
        # 添加dc:title
        dc_title = ET.SubElement(item, 'dc:title')
        dc_title.text = entry['info']['title']

        # 处理作者信息 - 每位作者单独一个dc:creator标签
        authors_list = entry['info'].get('authors', {}).get('author', [])
        if isinstance(authors_list, list):
            for author_info in authors_list:
                author = ET.SubElement(item, 'dc:creator')
                author.text = author_info['text']
        elif isinstance(authors_list, dict):
            author = ET.SubElement(item, 'dc:creator')
            author.text = authors_list['text']

        # 添加出版日期
        date = ET.SubElement(item, 'pubDate')
        year = entry['info'].get('year', "2000")  # 默认年份为 2000
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
        link.text = entry['info']['url']

        # 添加唯一标识符（guid）
        guid = ET.SubElement(item, 'guid')
        if 'key' in entry['info']:
            guid_value = entry['info']['key']
        else:
            guid_value = entry['info']['url']
            
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
            prism_pubname.text = entry['info']['venue']
            
            # dc:source (应该包含issn，但dblp没有这个信息，用venue代替)
            dc_source = ET.SubElement(item, 'dc:source')
            dc_source.text = entry['info']['venue']
            
        if 'volume' in entry['info']:
            prism_volume = ET.SubElement(item, 'prism:volume')
            prism_volume.text = entry['info']['volume']
            
        if 'number' in entry['info']:
            prism_number = ET.SubElement(item, 'prism:number')
            prism_number.text = entry['info']['number']
            
        if 'doi' in entry['info']:
            dc_identifier = ET.SubElement(item, 'dc:identifier')
            dc_identifier.text = f"doi:{entry['info']['doi']}"
            
        if 'ee' in entry['info']:
            dc_format = ET.SubElement(item, 'dc:format')
            dc_format.text = "text/html"
            
        # 添加dc:type
        dc_type = ET.SubElement(item, 'dc:type')
        dc_type.text = "text"

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
