#!/usr/bin/env sh
docker build -t scraper .
docker run --rm -p 8000:8000 -it --shm-size=2g --name foobar scraper
docker kill foobar
