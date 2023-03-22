from setuptools import setup

setup(
    name="firm_scrape",
    packages=["firm_scrape"],
    include_package_data=True,
    install_requires=["flask", "undetected-chromedriver"],
)
