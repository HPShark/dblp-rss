DBLP RSS
===

This is a small project to build a [RSS feed](https://en.wikipedia.org/wiki/RSS) from the [DBLP.org](https://dblp.org) public API

This is a quick and dirty solution. Use at your own risks.

# How the code works

- Sends a request to the DBLP API for the top 500 articles with a given keyword
- Receives the list of references
- Builds an RSS feed from those results
- Serves the feed with a minimalist HTTP server, listening on all local addresses, port 80
- The feed can be recovered at `http://localhost:80/dblp/<keyword>`

So, to run it, you need to :
- Get the code
- Build the docker image
- Run the image on a server, this spawns the http server
- Configure your RSS client to pull the feed from this webserver

# Configuration
By default, the code pulls the top 500 results.
This can be changed in the global variable in the beginning of file `dblp.py`

By default, the server runs on `0.0.0.0`, port `80`. This can be changed in the global variables in the beginning of 
file `server.py`

# Content

- `Dockerfile` : A docker image to deploy the RSS server
- `dblp.py` : handles the queries to DBLP and converting the results to the right RSS format
- `server.py` : deploys a minimalist HTTP server to serve the result of the `dblp.py` file, and make it accessible to remote RSS aggregators