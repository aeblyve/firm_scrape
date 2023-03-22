#FROM datawookie/undetected-chromedriver
FROM ultrafunk/undetected-chromedriver

RUN apt update -y && apt upgrade -y

RUN pip3 install flask flask-cors selenium nltk flask-login sqlalchemy ultimate_sitemap_parser

COPY firm_scrape/ /firm_scrape/

WORKDIR firm_scrape

CMD [ "pip", "install", "-e", "." ]

CMD ["flask", "--app", "firm_scrape", "run", "--host=0.0.0.0", "--port=8000"]
