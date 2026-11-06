# DBLP RSS转换器部署

将 DBLP 期刊和会议的论文更新转换为 RSS，供 Zotero 等阅读器订阅。数据来自官方 XML 总库，不再依赖可能触发反爬验证的搜索 JSON API。



项目参考自：[Pantoofle/dblp-rss: A quick and dirty RSS server to translate DBLP API results to a standard RSS flux](https://github.com/Pantoofle/dblp-rss)

并做了以下修改，以便适应小范围部署，满足实验室成员获取论文的需求~：

- 启动时检查本地数据：没有总库时自动下载，有数据时立即提供已有 RSS。
- 每 24 小时检查官方压缩总库，有变化才下载；同步时间持久化，重启不会重复下载。普通下载故障每小时重试，容量故障每 5 分钟重试；仍在缓存中的 RSS 继续可用。
- 同一次下载中如连接中断，最多重试 4 次；服务器支持时通过 `Range` / `If-Range` 续传，并在完成后验证 MD5 和 gzip/XML 完整性。若服务器不支持续传，则重新下载；最终失败后清理临时文件。
- RSS 先按论文发表年份倒序；期刊同年再按数值卷号倒序，会议同年不按卷号；同年同卷内再按 `mdate`（DBLP 记录修改日期）和 DBLP key 倒序。发表年份优先于 DBLP 修改日期，修订过的旧论文不会排在新论文前面。
- 每个期刊/会议保留按上述顺序最新的 300 条，与“最近 30 天内所有 DBLP 修改记录”的并集。这样近期一次性录入的大批论文仍会全部进入 RSS；其中发表较早的论文排在较后位置。RSS `pubDate` 使用发表年份；DBLP 总库只提供年份时以 1 月 1 日表示，`dc:date` 保留年份，描述中另列 DBLP 修改日期。
- 当前 SQLite 已保存论文年份和期刊卷号；仅改变 RSS 排序无需重新下载或扫描总库，下一次响应即可按新顺序显示。新订阅和每日刷新也按同样顺序筛选 300 条。
- 只把已经请求过的订阅写入 SQLite，不存完整数据库。保留一份最新 `.xml.gz`，流式解压解析，不落地解压后的 XML；新版本完整校验并安装后立即删除旧版本，失败/重启遗留的临时文件自动清理。总库安装与论文缓存发布独立，缓存写入失败不会撤回已校验的新总库。
- 全量建立期刊/会议名称目录，支持 `/search` 页面按名称、简称或 DBLP 标识搜索、分类、分页和复制 RSS 链接。目录包含所有从当前快照识别出的期刊/会议，不受已订阅来源数量限制；论文记录仍按需缓存。
- 自动记录 RSS 请求时间，每小时检查一次，淘汰连续 30 天无人访问的来源论文缓存。搜索和后台同步不算访问；来源名称目录一直保留，再次请求后从本地总库重建。
- 数据目录预算为 9 GB，单个数据库上限约 1.5 GB。压缩包也受同一 9 GB 总预算约束，仍需为目录与导入留空间，不再受旧版 2 GB 的固定限制。空间不足时先清理过期缓存，必要时删除旧压缩总库，再按最后访问时间从久到近淘汰其他来源，为更新腾出空间。每次淘汰完整来源，不把近期记录静默截断为 300 条。
- 数据库使用增量空间回收；旧数据库首次升级在空间允许时自动迁移。预留约 8 MB 紧急空间供低磁盘清理使用；临时库、事务日志和新旧压缩包均计入同步预算。该限制不含 Python 环境、Docker 镜像和宿主机自行配置的 Docker 日志。
- 请求日志自动轮转，最多约 20 MB；避免把访问日志重复输出到容器控制台，多进程间通过文件锁避免重复同步。

## 本地 Python 运行与测试（不需要 Docker）

Windows PowerShell 使用项目已有的 `venv`：

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe server.py
```

监听 `0.0.0.0:8080`。本机访问使用 `http://127.0.0.1:8080`，服务本身不直接提供 HTTPS。

自动化测试使用临时目录和本地 HTTP 模拟总库，不下载完整线上总库，不改动已有缓存：

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

截至 2026-11-06 本地验证：项目 `venv` 中 56 项自动化测试通过，覆盖按发表年份/期刊卷号排序、旧论文更新后降序、会议排序和 300 条发表顺序筛选，以及此前的新增订阅合批、磁盘容量、淘汰、并发和重启测试；另下载并校验完整官方总库，流式扫描 13,074,484 条记录，建立 8,651 个来源的目录（1,913 个期刊、6,738 个会议来源）。此前快照缓存的 TDSC、Eurocrypt、CVPR 分别返回 300、300、2,265 条；整个 `cache` 目录约 1.16 GB（含旧版缓存和紧急预留文件）。实际浏览器测试已通过，覆盖搜索、复制链接、域名端口、分类、分页和手机布局。占用和条数会随快照、订阅变化。本地未安装或运行 Docker；已静态核对 Dockerfile 的端口和数据卷配置，镜像构建仍按原部署方式在服务器进行。

已有完整压缩总库时，还可手动运行完整扫描合批验证（耗时数分钟，不会再次下载总库）：

```powershell
$snapshot = Get-ChildItem cache -Filter 'dblp-*.xml.gz' | Select-Object -First 1
.\venv\Scripts\python.exe tests/full_snapshot_batching.py $snapshot.FullName
```

该脚本在临时目录创建独立数据库，只读已有总库与同目录的 `dblp.dtd`。首轮实际扫描到 25 万条记录后提交 10 个不同来源及重复请求，检查两轮扫描的来源数为 `[1, 10]`、11 个 RSS 均有数据，且空闲时不再扫描；退出时清理测试数据库。

2026-11-04 完整总库合批实测通过：两轮各扫描 13,074,484 条记录，来源数为 `[1, 10]`，分别耗时约 245 秒、155 秒（首轮同时构建目录）。11 个来源的 RSS 均非空，待处理列表清空，重复请求后的额外空闲扫描为 0 次，网络同步为 0 次。耗时仅代表本地测试机器。

## 订阅与同步状态

| 入口 | IP 访问示例 | 域名访问示例 | 用途 |
| --- | --- | --- | --- |
| 来源搜索 | `http://ip:8080/search` | `http://你的域名:8080/search` | 查找期刊/会议，复制 RSS 订阅链接 |
| 运行状态 | `http://ip:8080/health` | `http://你的域名:8080/health` | 查看总库同步、缓存容量及自动淘汰情况，返回 JSON |
| 来源目录 API | `http://ip:8080/api/venues?q=TDSC` | `http://你的域名:8080/api/venues?q=TDSC` | 通过 JSON 查询可订阅来源 |

访问服务根路径 `/` 会自动跳转到 `/search`。以下 IP 和域名示例都保留 `:8080`。

### `/search`：搜索期刊和会议并获取 RSS 链接

访问 `http://ip:8080/search` 或 `http://你的域名:8080/search` 搜索全部来源。输入 `TDSC`、`CVPR`、`Eurocrypt` 或英文名称，会返回同一服务下的 RSS 链接；复制的地址自动使用浏览器当前的协议、IP/域名和端口，域名访问时保留 `:8080`。页面支持实时搜索、期刊/会议筛选和分页，不依赖外部 CDN 或线上 DBLP 搜索 API。

使用步骤：

1. 打开 `/search`，输入期刊/会议简称、英文名称或 DBLP 标识，例如 `TDSC`、`Computer Vision and Pattern Recognition`、`conf/eurocrypt`。
2. 按需选择“全部”“期刊”或“会议”，查看匹配结果；也可留空浏览完整目录并翻页。
3. 点击“复制链接”，把完整地址添加到 Zotero 或其他 RSS 阅读器；“打开 RSS”用于直接查看该订阅。
4. 显示“RSS 已准备好”的来源已有可读取缓存。尚未缓存或已被淘汰的来源会在首次访问后后台解析；返回 `503` 时稍后重试同一链接即可。

页面会显示目录来源数量和更新时间。首次建立目录时会提示准备中，并自动刷新。也可以直接打开带查询参数的页面，例如 `http://你的域名:8080/search?q=TDSC&kind=journals`。

**目录是全量的，论文缓存是按需的。** 三个测试来源不是白名单；其他来源点击“打开 RSS”后会自动登记并解析。搜索本身不会登记订阅或下载总库。升级后如果缺少目录，程序会在后台扫描已有压缩包一次，不会为建立目录重新下载总库；准备过程中页面自动刷新结果。

目录接口：`GET /api/venues?q=TDSC&kind=all&limit=30&offset=0`。`kind` 支持 `all`、`journals`、`conf`，单页最多 100 条；响应包含目录准备状态、匹配数量和各来源的相对 RSS 链接、缓存准备状态。常见英文缩写和全称会归一化检索，名称/别名来自 XML 的期刊名、会议名及会议录标题。

### RSS 订阅地址与首次访问

原来的访问路径不变，支持 IP、域名和原有反向代理：

```text
http://ip:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A
http://ip:8080/dblp/stream%3Astreams%2Fconf%2Feurocrypt%3A
http://你的域名:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A
```

默认 IP 和域名访问均显式使用 `:8080`，服务提供 HTTP。只有另外配置了 TLS 代理时才使用 HTTPS；搜索页按实际浏览器地址生成链接，不会自动去掉端口。原有 Docker 端口映射仍为 `8080:8080`。

首次请求会自动登记订阅。首次下载总库或首次解析新订阅期间返回 `503` 和 `Retry-After: 60`；准备完成后重复访问同一链接即可得到 RSS。新增订阅从本地压缩总库解析，不额外下载总库。解析期间其他已有订阅继续可用。

**新增订阅按批次合并，按来源去重。** 每轮扫描开始时固定该轮来源名单；扫描完成后，立即重新读取全部待处理来源，一次扫描处理下一整批。例如：

1. `t` 时刻收到来源 A 的首次请求，开始第 1 轮全库扫描。
2. 假设扫描需要 60 秒，这 60 秒内新增 10 个不同来源 B–K，均先登记等待；对 A 或 B–K 的重复请求不会创建重复任务。
3. 第 1 轮完成并发布 A 后，立即启动第 2 轮，一次处理 B–K 这 10 个来源。没有其他新增来源时，总共只扫描两轮。
4. 第 2 轮期间才到达的新来源进入第 3 轮；已准备好的来源反复访问不会触发扫描。每日新总库扫描期间到达的请求也按同样方式在后续批次处理。

60 秒只是上述时序示例，真实耗时取决于总库大小和机器性能。合批减少扫描次数，但每轮仍需流式读取整个压缩总库，因此扫描期间 CPU 较高是可能的；无待处理来源、无目录重建、且未到每日同步时间时不会全库扫描。导入失败遵守重试间隔，新请求不会绕过退避；同一轮因空间不足被淘汰的来源不会立即反复重建。

服务器日志会输出 `Starting subscription batch N: X sources`，随后列出扫描来源（最多显示前 20 个）、是否同时构建目录，以及 `Completed snapshot scan: ... records in ...s` 的实际耗时。`pending_subscriptions` 包含正在处理和等待下一轮的来源；应结合这些日志判断连续扫描是在处理不同批次，还是发生了失败重试。

当前支持 `stream:streams/journals/<标识>:` 与 `stream:streams/conf/<标识>:`，不支持任意 DBLP 搜索表达式。完整目录准备好后，不存在的标识直接返回空 RSS，不登记订阅或触发全库扫描；过长或格式错误的查询返回 `400`。RSS `pubDate` 按发表年份生成，GUID 使用持久的 DBLP 记录链接；DBLP 修改日期仍显示在条目描述中。

### `/health`：查看同步进度、容量与自动淘汰状态

浏览器访问 `http://ip:8080/health` 或 `http://你的域名:8080/health`，会返回当前状态 JSON。也可以在服务器或本机使用：

```bash
curl http://127.0.0.1:8080/health
```

| 字段 | 含义 |
| --- | --- |
| `ready` | 当前元数据指向的本地压缩总库文件是否存在；不表示所有 RSS 缓存都已经更新 |
| `catalog_ready` | 来源名称目录是否已建立，供 `/search` 查询 |
| `venue_count` / `catalog_updated_at` | 目录中的来源数量 / 目录最近一次发布时间 |
| `subscriptions` | 当前登记的来源数量，包括等待解析的来源；不等于完整目录数量 |
| `pending_subscriptions` | 等待首次解析或需要刷新到最新快照的来源标识列表 |
| `last_sync_at` | 最近一次成功安装已校验总库的时间 |
| `last_feed_sync_at` | 最近一次成功发布论文缓存的时间；与总库安装时间分开记录 |
| `last_checked_at` / `next_sync_at` | 最近一次成功检查上游的时间 / 下次计划检查或重试的时间 |
| `last_error` | 最近记录的错误信息；正常清除错误后为空字符串 |
| `cache_bytes` / `cache_budget_bytes` | 当前缓存目录总占用 / 同步容量预算，单位为字节；默认预算为 9 GB |
| `database_bytes` / `database_budget_bytes` | 正式 SQLite 文件大小 / 单数据库预算，单位为字节；默认预算约 1.5 GB |
| `cold_days` | 无 RSS 请求多少天后按冷缓存清理，默认 30 天 |
| `evicted_sources_total` | 累计自动淘汰的来源次数；同一来源重建后再次淘汰会再次计数 |
| `last_evicted_stream` / `last_eviction_at` | 最近一次被淘汰的来源 / 淘汰时间 |
| `last_snapshot_discarded_at` | 为腾出更新空间而提前删除旧总库的最近时间 |

容量字段以字节计，除以 `1,000,000,000` 可换算为本说明使用的 GB。`next_sync_at` 是 Unix 秒时间戳，JSON 中可能是字符串；其他时间字段通常是带 UTC 时区的 ISO 8601 字符串，尚未产生的值可能为 `null`。

常见状态的判断：

- **首次初始化**：`ready=false` 或 `catalog_ready=false`，通常在下载总库或建立目录。等待后台任务完成即可，无需反复重启容器。
- **部分来源准备中**：已有总库，但 `pending_subscriptions` 非空，表示部分订阅尚在解析或追赶新快照；仍有缓存的来源可继续读取。
- **缓存被淘汰**：`evicted_sources_total` 增长，可能是来源 30 天无人访问或更新需要腾空间；这本身不是同步故障，目录仍保留，下一次访问会触发重建。
- **同步或缓存发布失败**：`last_error` 非空时，结合两个更新时间和待处理列表判断影响范围，并在服务器执行 `docker logs -f dblp-rss` 查看原因。已安装的新总库可能仍然可用，缓存会独立重试。

`/health` 用作**服务存活检查**，在正常返回状态时 HTTP 为 `200`，即使首次初始化尚未完成。监控是否“准备好”或是否同步正常，还需要检查 JSON 字段，不能只看 HTTP 状态码。

### 数据持久化与升级

所有同步数据保存在原来挂载的 `cache` 目录，不需要额外挂载或定时任务。`DBLP_CACHE_DIR` 可选地修改数据目录，`DBLP_DUMP_URL` 可选地修改总库下载地址；正常部署无需配置。

升级后使用新的 `cache/dblp_local.db`。旧版 `dblp_cache.db`、`dblp_cache.pkl` 不再用于 RSS，程序不会擅自删除；确认升级成功后可自行删除这两个旧文件。请勿删除正在使用的 `dblp_local.db`、`dblp.dtd` 或当前 `dblp-<校验值>.xml.gz`。

## 容量估算与超限行为

2026-11-04 对本地完整快照做了只读容量统计，没有把全部论文导入正式数据库：8,651 个来源按现有保留规则合计需要 1,588,578 条记录，其中程序当时的最近 30 天窗口内有 194,775 条记录，49 个来源的近期记录超过 300 条。当前压缩总库为 1,116,322,485 字节；使用中的 SQLite 约 16 MB，其中完整目录和元数据约 14.6 MB，三个订阅的论文及索引约 1.44 MB。

按当前论文缓存的实际平均占用估算，全部来源的论文及索引约 0.8 GB；为记录长度、索引与文件空洞留余量，可按 1–1.5 GB 规划数据库。加上总库，常驻数据约 2–3 GB；新旧压缩包、正式库、临时库和事务日志并存时，更新峰值可按约 4–7 GB 估算。这些是当前快照的规划估算，不是全量入库实测或未来容量保证；上游集中修改历史记录时，最近 30 天的保留量可能明显增加。

当前代码已把单数据库预算提高到约 1.5 GB，并实现自动淘汰。过期缓存优先清除；更新空间不足时可先删除旧压缩总库，再按最后访问时间淘汰整组期刊/会议缓存；临时导入数据也同步删除，避免刚淘汰又被导入。淘汰后会回收数据库文件中的空闲页，名称目录保留；被淘汰的来源再次请求时返回 `503` 并从本地最新总库重建。历史非增量回收数据库会在空间允许时自动迁移。

已校验的新总库优先安装，RSS 缓存事务失败时保留旧的完整 RSS，并从新总库独立重试；重启也能识别尚未刷新的缓存。低容量清理包含约 8 MB 紧急预留空间、分批删除和增量回收，避免为清理再复制整个数据库；大量来源的旧记录候选会分批落盘，避免在内存中同时堆积所有来源的 300 条记录。

9 GB 是应用同步预算，不是操作系统级磁盘配额，且不包含 Docker 镜像和宿主机 Docker 日志。程序不会擅自删除其他应用文件或旧版 API 缓存。若磁盘连最新总库和最小目录都容纳不下，或上游网络不可用，无法保证立刻完成更新，会记录原因并自动重试；空间不足时可能提前删除旧总库，此后若下载失败，已有缓存仍可读，新订阅需等待下载恢复。单个来源若自身超过数据库预算，会整组淘汰而不返回残缺 RSS。

## 服务器配置过程

### 开启防火墙

如果是走cloudflarecdn的话只能是以下几个端口，这里使用8080：

**Cloudflare 支持的 HTTP 端口：**

- 80
- 8080
- 8880
- 2052
- 2082
- 2086
- 2095

**Cloudflare 支持的 HTTPS 端口：**

- 443
- 2053
- 2083
- 2087
- 2096
- 8443

#### 使用firewalld，这里以甲骨文的vps为例

```
sudo apt install firewalld
sudo firewall-cmd --zone=public --permanent \
--add-port=22/tcp --add-port=22/udp \
--add-port=80/tcp --add-port=80/udp \
--add-port=8080/tcp --add-port=8080/udp \
--add-port=443/tcp --add-port=443/udp \
--add-port=2053/tcp --add-port=2053/udp \
--add-port=2083/tcp --add-port=2083/udp \
--add-port=8087/tcp --add-port=8087/udp \
--add-port=2096/tcp --add-port=2096/udp \
--add-port=8443/tcp --add-port=8443/udp
sudo firewall-cmd --reload
```

> 查看防火墙状态：sudo firewall-cmd --zone=public --list-ports
>
> 查看防火墙当前规则：sudo firewall-cmd --list-all
>
> 确认特定端口是否开放：sudo firewall-cmd --zone=public --query-port=8080/tcp

#### 使用x-ui自带的iptables管理工具

输入`x-ui`选择21，开启防火墙并放行端口，将上述端口放行即可



### 安装docker

使用runoob教程的方案，地址：[Ubuntu Docker 安装 | 菜鸟教程](https://www.runoob.com/docker/ubuntu-docker-install.html)

1. 安装docker
   ```
   curl -fsSL https://test.docker.com -o test-docker.sh
   sudo sh test-docker.sh
   ```

2. 测试Helloworld：
   ```
   docker run ubuntu:15.10 /bin/echo "Hello world"
   ```

### 安装dblp-rss

1. 克隆项目代码
   ```
   git clone https://github.com/HPShark/dblp-rss.git
   ```

2. 进入项目目录，构建 Docker 镜像
   ```
   cd dblp-rss
   docker build -t dblp-rss .
   ```

3. 运行 Docker 容器
   ```
   docker run -d -p 8080:8080 -v $(pwd)/cache:/app/cache --restart always --name dblp-rss dblp-rss
   ```

   > `-p 8080:8080`：将服务器的 8080端口映射到容器的 8080端口。
   >
   > `--restart always`：设置容器在意外退出时自动重启。
   >
   > `--name dblp-rss`：将容器命名为 `dblp-rss`。

4. 验证运行状态
   ```
   docker ps
   ```

   确认 `dblp-rss` 容器正在运行，并且 `PORTS` 列显示 `0.0.0.0:8080->8080/tcp`。

5. 访问：`http://ip:8080/dblp/<keyword>`

   > keyword指q值的内容，比如这个网址：
   >
   > `https://dblp.org/search/publ/api?q=stream%3Astreams%2Fjournals%2Ftdsc%3A&h=1000&format=json`
   >
   > keyword 使用 `stream%3Astreams%2Fjournals%2Ftdsc%3A`，浏览器访问 `http://ip:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A` 即可订阅 TDSC。每个来源保留最新 300 条，以及最近 30 天更新的全部记录；直接把链接添加进 Zotero 等 RSS 软件即可。

6. 访问 `http://ip:8080/health` 检查初始化与同步状态，使用 `docker logs -f dblp-rss` 查看下载及解析日志。同步由程序自动完成，无需添加每天重启容器的 cron；已有此 cron 可以移除，避免中断下载。

> 查看日志：docker logs dblp-rss
>
> 重启容器：docker start dblp-rss
>
> 停止容器：docker stop dblp-rss
>
> 删除容器，但不删除镜像和数据：docker rm dblp-rss
>
> 删除镜像：docker rmi dblp-rss
>
> 查看所有容器（包括停止的）：docker ps -a
>
> 一键启动：
>
> ```
> cd dblp-rss/
> docker build -t dblp-rss .
> docker run -d -p 8080:8080 -v $(pwd)/cache:/app/cache --restart always --name dblp-rss dblp-rss
> ```
>
> 一键删除：
>
> ```
> docker stop dblp-rss
> docker rm dblp-rss
> docker rmi dblp-rss
> ```



## cloudflare配置ssl

以下保留原有的外部 443 端口代理方案。日常按域名加 `8080` 直连时，使用 `http://你的域名:8080/search` 和 `http://你的域名:8080/dblp/<keyword>`。

一般情况下网站的SSL/TLS 全局加密模式为：完全（严格），这里通过特殊规则的形式为dblp rss配置单独ssl。

1. dns设置域名，比如dblp.xxx.xxx，并**开启**cdn小云朵代理。
2. `规则-origin rules`里头新建一个dblp规则，进行如下配置，将页面访问的443端口重定向到8080：
   - 选择`自定义筛选表达式`
   - 字段：主机名
   - 运算符：等于
   - 值填写：dblp.xxx.xxx
   - 目标端口重写到：8080
   - 保存

3. `规则-页面规则`里新建一个规则，将该域名的ssl配置修改为`灵活`：
   - URL：dblp.xxx.xxx
   - 选取设置：SSL
   - 选择 SSL/TLS 加密模式：灵活
   - 保存页面规则
4. 访问：`https://dblp.xxx.xxx/dblp/<keyword>`
