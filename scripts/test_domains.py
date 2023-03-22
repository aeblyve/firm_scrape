# For each domain in law_firms, check the response code.

import requests
import time
import asyncio
import aiohttp

loc = "./law_firms.csv"

start = time.time()


async def get(domain, session):
    url = f"https://{domain}"
    try:
        async with session.get(url=url) as response:
            if response.status != 200:
                print(f"{domain}")
    except Exception as e:
        print(f"{domain} exception")

async def main(urls):
    async with aiohttp.ClientSession() as session:
        ret = await asyncio.gather(*[get(url, session) for url in urls])
    print("Finalized all. Return is a list of len {} outputs.".format(len(ret)))



with open(loc, "r") as f:
    asyncio.run(main(f.read().splitlines()))

end = time.time()

print("Took {} seconds to pull {} websites.".format(end - start, len(urls)))
