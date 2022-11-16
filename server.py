import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from dblp import dblp_rss

hostName = '0.0.0.0'
serverPort = 80


class RSSServer(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/dblp/'):
            keyword = self.path.split('/')[-1]
            self.send_response(200)
            self.send_header("Content-type", "application/rss+xml")
            self.end_headers()
            self.wfile.write(bytes(dblp_rss(keyword), "utf-8"))
        else:
            self.send_response(404)


if __name__ == "__main__":
    webServer = HTTPServer((hostName, serverPort), RSSServer)
    print("Server started http://%s:%s" % (hostName, serverPort))

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass

    webServer.server_close()
    print("Server stopped.")
