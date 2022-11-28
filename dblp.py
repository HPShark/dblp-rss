import requests
import json
import datetime
from xml.etree import ElementTree as ET

###########################################
# How many articles must be pulled
NB_ENTRIES = 500
#################################################


def get_json_from_dblp(keyword: str, nb_entries: int):
    BASE_URL = "https://dblp.org/search/publ/api"
    parameters = {"q": keyword,
                  "h": nb_entries,
                  "format": "json",
                  "c": 0}

    res = requests.get(BASE_URL, params=parameters)
    if res.status_code == 200:
        return json.loads(res.content)
    else:
        raise ValueError(f"Error {res.status_code} when fetching DBLP for keyword {keyword}")


def generate_rss_feed(json_data):
    """ Formats the json result from DBLP to a valid RSS file. Only works with a valid answer from DBLP """

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
        authors_list = entry['info']['authors']['author'] if 'authors' in entry['info'] else "NULL"
        if type(authors_list) == list:
            author.text = ' | '.join(a['text'] for a in authors_list)
        elif type(authors_list) == dict:
            author.text = authors_list['text']
        date = ET.SubElement(item, 'pubDate')
        d = datetime.datetime(int(entry['info']['year']), 1, 1)
        date.text = d.strftime("%a, %d %b %Y %H:%M:%S %z")
        link = ET.SubElement(item, 'link')
        link.text = entry['info']['url']
        description = ET.SubElement(item, 'description')
        description.text = '\n'.join(
            f"{key}: {val}" for key, val in entry['info'].items() if key in ['venue', 'year', 'ee'])

    ET.indent(rss)
    return ET.tostring(rss, method='xml', encoding="unicode")


def dblp_rss(keyword):
    return generate_rss_feed(get_json_from_dblp(keyword, NB_ENTRIES))


if __name__ == '__main__':
    print(dblp_rss("whois"))
