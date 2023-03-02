from http.server import BaseHTTPRequestHandler, HTTPServer

from dblp import dblp_rss

hostName = '127.0.0.1'
serverPort = 8080


class RSSServer(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/dblp/'):
            keyword = self.path.split('/')[-1]
            try:
                data = dblp_rss(keyword)

                self.send_response(200)
                self.send_header("Content-type", "application/rss+xml")
                self.end_headers()
                self.wfile.write(bytes(data, "utf-8"))
            except ValueError as e:
                self.send_response(500)
                print(e)
        else:
            self.send_response(404)


if __name__ == "__main__":
    print(f"Starting the server http://{hostName}:{serverPort}...")
    webServer = HTTPServer((hostName, serverPort), RSSServer)
    print(f"Server started http://{hostName}:{serverPort}")

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass

    webServer.server_close()
    print("Server stopped.")
