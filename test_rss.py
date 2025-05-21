from dblp import dblp_rss
import os

# 确保test目录存在
os.makedirs('test', exist_ok=True)

# 获取RSS内容并保存到文件
rss_content = dblp_rss('stream:streams/journals/tdsc:')
with open('test/sample.xml', 'w', encoding='utf-8') as f:
    f.write(rss_content)

print(f"已保存RSS样本到 test/sample.xml") 