import requests
import json
from xml.etree import ElementTree as ET


def get_json_from_dblp(keyword: str, nb_entries: int):
    BASE_URL = "https://dblp.org/search/publ/api"
    parameters = {"q": keyword,
                  "h": nb_entries,
                  "format": "json",
                  "c": 0}

    res = requests.get(BASE_URL, params=parameters)
    return json.loads(res.content)


def generate_rss_feed(json_data):
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

    channel = ET.SubElement(rss, 'channel')

    title = ET.SubElement(channel, 'title')
    title.text = "DBLP"
    link = ET.SubElement(channel, 'link')
    link.text = "https://dblp.org/"
    description = ET.SubElement(channel, 'description')
    description.text = "Custom DBLP extraction from XML"
    language = ET.SubElement(channel, 'language')
    language.text = 'en'

    for entry in json_data['result']['hits']['hit']:
        item = ET.SubElement(channel, 'item')
        title = ET.SubElement(item, 'title')
        title.text = entry['info']['title']
        author = ET.SubElement(item, 'dc:creator')
        authors_list = entry['info']['authors']['author']
        if type(authors_list) == list:
            author.text = ' | '.join(a['text'] for a in authors_list)
        elif type(authors_list) == dict:
            author.text = authors_list['text']
        date = ET.SubElement(item, 'pubDate')
        date.text = entry['info']['year']
        link = ET.SubElement(item, 'link')
        link.text = entry['info']['url']
        description = ET.SubElement(item, 'description')
        description.text = '\n'.join(f"{key}: {val}" for key, val in entry['info'].items() if key != 'authors')

    return ET.tostring(rss, method='xml')


def dblp_rss():
    return generate_rss_feed(get_json_from_dblp('dns', 100))


if __name__ == '__main__':
    print(dblp_rss())
