# DBLP RSS转换器部署

dblp默认的前100条只有xml和json格式，zotero识别不了，github上有个开源项目可以将DBLP的api返回结果转换成RSS格式，方便zotero识别。



项目参考自：[Pantoofle/dblp-rss: A quick and dirty RSS server to translate DBLP API results to a standard RSS flux](https://github.com/Pantoofle/dblp-rss)

并做了以下修改，以便适应小范围部署，满足实验室成员获取论文的需求~：

- 解决了部分bug问题。
- 增加了排序，dblp的api返回的结果不是按时间排序，本代码使用了稳定排序按照time>Volume>Number优先级排序，并保证了相对顺序不改变。
- 增加了缓存功能，24h内请求同一个keyword直接返回缓存结果，缓解服务器压力。

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
   > 其中keyword对应的就是q的参数`stream%3Astreams%2Fjournals%2Ftdsc%3A`，故浏览器访问`http://ip:8080/dblp/stream%3Astreams%2Fjournals%2Ftdsc%3A `就可以访问tdsc这个期刊最近1000篇论文的rss转换结果，直接把这个域名添加进zotero等rss订阅链接里就能自动识别

6. 设置docker自动重启，当前docker运行时间长会崩溃，原因未知。

   ```
   crontab -e
   添加以下任务，每天凌晨3点重启容器：
   0 3 * * * docker restart dblp-rss
   ```

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