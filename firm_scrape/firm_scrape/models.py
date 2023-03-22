from datetime import datetime
import enum
from firm_scrape.util import setup_webdriver
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Enum,
    ARRAY,
    DateTime,
    text,
    ForeignKey,
)
from firm_scrape.database import Base
from sqlalchemy.orm import mapped_column, relationship
from .constants import (
    LAW_FIRM_KEY_PRACTICES,
    LAW_FIRM_KEY_TITLES,
    INVESTMENT_BANK_KEY_TITLES,
    LAW_FIRM_TEAM_PAGE_KEYWORDS,
    TEAM_PAGE_KEYWORDS,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import Select
from .util import setup_webdriver, get_profile_selector
import time
import logging
from urllib import parse
from firm_scrape.database import db_session
import requests
from usp.tree import sitemap_tree_for_homepage
from selenium.webdriver.common.keys import Keys


class FirmType(enum.Enum):
    LAW = 1
    INVESTMENT = 2


class FirmJob(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)

    domain = Column(String(100), unique=True)
    firm_type = Column(Enum(FirmType))

    completed = Column(Boolean)
    failed = Column(Boolean)
    fail_reason = Column(String(500))

    start_time = Column(DateTime)
    end_time = Column(DateTime)

    count = Column(Integer)
    limit = Column(Integer)

    team_url = Column(String(100))

    profiles = relationship("PersonalProfile", back_populates="job")

    def __init__(self, domain, firm_type, limit):
        self.domain = domain
        self.firm_type = firm_type
        self.limit = limit
        self.count = 0
        self.completed = False
        self.failed = False
        self.fail_reason = "N/A"

    def __repr__(self):
        return self.domain

    def execute(self, name_set):
        team_fail_reason = ""
        # TODO maybe fail reason for both strategies
        try:
            self.execute_team_page_strategy(name_set)
        except Exception as e:
            logging.warning(
                "Team page strategy failed for {self.domain}, switching to sitemap strategy."
            )
            team_fail_reason = str(e)
            logging.error(e)
            try:
                self.execute_sitemap_strategy()
            except Exception as e:
                logging.error(e)
                self.failed = True
                self.fail_reason = team_fail_reason
        self.completed = True

    def execute_sitemap_strategy(self):
        self.start_time = datetime.now()
        driver = setup_webdriver()
        url = f"http://{self.domain}"
        driver.get(url)
        time.sleep(5)

        tree = sitemap_tree_for_homepage(url)

        profile_hrefs = []

        for page in tree.all_pages():
            url = page.url
            parsed = parse.urlparse(url)

            team_page_keywords = TEAM_PAGE_KEYWORDS
            if self.firm_type == FirmType.LAW:
                team_page_keywords += LAW_FIRM_TEAM_PAGE_KEYWORDS

            for keyword in team_page_keywords:
                if keyword in parsed.path:
                    profile_hrefs += url
                    break
        logging.info(f"Url inspection found {len(profile_hrefs)} profile hrefs.")

        profiles = []
        for profile_href in profile_hrefs:
            driver.get(profile_href)
            time.sleep(5)

            profile = PersonalProfile(profile_href, self.firm_type, self.id)
            profiles += profile

            root = driver.find_element(By.XPATH, "/*")
            profile.update_with_full_element(root)

        for profile in profiles:
            db_session.add(profile)
            try:
                db_session.commit()
            except:
                db_session.rollback()
        logging.info(f"Info in {self.domain}: Finished!")

    def execute_team_page_strategy(self, name_set):
        self.start_time = datetime.now()
        driver = setup_webdriver()

        url = f"http://{self.domain}"

        driver.get(url)

        time.sleep(5)

        anchors = driver.find_elements(By.TAG_NAME, "a")
        if len(anchors) == 0:
            logging.error(f"Error in {self.domain}: had no anchors.")

        for anchor in anchors:
            href = anchor.get_dom_attribute("href")
            if href is not None:
                parsed = parse.urlparse(href)

                team_page_keywords = TEAM_PAGE_KEYWORDS
                if self.firm_type == FirmType.LAW:
                    team_page_keywords += LAW_FIRM_TEAM_PAGE_KEYWORDS

                for keyword in team_page_keywords:
                    if keyword in parsed.path:
                        team_url = parse.urljoin(url, href)
                        logging.info(
                            f"Info in {self.domain}: Found team page for {url}: {team_url}"
                        )
                        self.team_url = team_url
                        return self.process_team_page(driver, name_set)
        raise Exception("Failed to find a team page. Is the page still up?")

    def process_team_page(self, driver, name_set):
        """
        Processes the team page - finds relevant search functionality, and uses it. Then, delegates to scrape_team_page to do the actual scraping.
        """
        driver.get(self.team_url)
        time.sleep(5)

        # apply search
        # For each search query, populate every page, if it exists
        # So, fork the profile gathering into its own method

        select_tags = driver.find_elements(By.TAG_NAME, "select")

        key_selects = []

        for select_tag in select_tags:
            key_options_text = []
            select = Select(select_tag)
            for option in select.options:
                print(option)
                print(option.text)
                option_str = option.text.lower()
                if (
                    option_str in LAW_FIRM_KEY_PRACTICES
                    or option_str in LAW_FIRM_KEY_TITLES
                    or option_str in INVESTMENT_BANK_KEY_TITLES
                ):
                    key_options_text.append(option.text)
            if key_options_text:
                key_selects.append((select, key_options_text))

        # now, unfold key_selects

        logging.info(key_selects)

        search_configs = []

        def add_configs(idx, configs, config):
            if idx == len(key_selects):
                configs.append(config)
                return
            select, key_options_text = key_selects[idx]
            for key_option_text in key_options_text:
                new_config = config + [(select, key_option_text)]
                add_configs(idx + 1, configs, new_config)

        add_configs(0, search_configs, [])
        logging.info(search_configs)

        config_count = len(search_configs)

        # TODO search only with law firms, not worth otherwise

        if not (config_count == 1 and search_configs[0] == []):

            failed_config_count = 0

            for (
                search_config
            ) in (
                search_configs
            ):  # TODO Non-select (div, etc) filtering
                for select, option_text in search_config:
                    print(select)
                    logging.info("executing select")
                    select.select_by_visible_text(option_text)
                    time.sleep(0.5)
                logging.info("executing search")
                search_button = self.find_search_button(driver)
                driver.execute_script("arguments[0].click();", search_button)
                time.sleep(5)
                logging.info("executing scrape")
                try:
                    self.scrape_team_page(driver, name_set)
                except:
                    logging.info("No results for filter.")
                    failed_config_count += 1

            if failed_config_count == config_count:
                raise Exception(
                    "No filtering configuration was effective."
                )  # TODO do we always want to raise exception?
        elif self.firm_type == FirmType.LAW:
            # attempt to use the input box
            logging.info("No select elements found, trying search_box strategy")
            search_box = self.find_search_box(driver)
            failed_search_count = 0
            if search_box is not None:
                logging.info(f"Got search box. {search_box.text}")
                for (
                    key_practice
                ) in (
                    LAW_FIRM_KEY_PRACTICES
                ):  # HACK This is the best filter, generally, for what we want.

                    logging.info("Sending keys.")
                    search_box.send_keys(key_practice)
                    search_box.send_keys(Keys.RETURN)

                    # search_button = self.find_search_button(driver)
                    # driver.execute_script("arguments[0].click();", search_button)
                    time.sleep(5)
                    logging.info("executing scrape")
                    try:
                        self.scrape_team_page(driver, name_set)
                        search_box = self.find_search_box(driver)
                    except:
                        logging.info(
                            "No results for search."
                        )  # TODO sendkeys to empty box
                        failed_search_count += 1
            if failed_search_count == len(LAW_FIRM_KEY_PRACTICES):
                raise Exception("No search configuration was effective.")
        else:
            self.scrape_team_page(driver, name_set)

    def find_search_button(self, driver):
        for button in driver.find_elements(By.TAG_NAME, "button"):
            if "search" in button.get_attribute("outerHTML").lower():
                return button

    def find_search_box(self, driver):
        for input_tag in driver.find_elements(By.TAG_NAME, "input"):
            if (
                "search" in input_tag.get_attribute("outerHTML").lower()
                and input_tag.is_displayed()
                and input_tag.is_enabled()
            ):
                return input_tag

    def scrape_team_page(self, driver, name_set):
        logging.info("Begin get profile class.")
        profile_class = get_profile_selector(driver, name_set)
        logging.info(f"Found profile class {profile_class}")

        page_index = [1]

        profiles = []
        exhausted = [False]

        while not exhausted[0]:
            exhausted[0] = True

            new_profiles = self.skim_team_page(name_set, driver, profile_class)
            for new_profile in new_profiles:
                if not new_profile.contains_email():
                    full_element_href = new_profile.get_full_element_href()
                    self.visit_full_profile_href(driver, new_profile, full_element_href)

            profiles += new_profiles

            if len(profiles) == self.limit:
                break

            self.get_next_if_exists(driver, page_index, exhausted)

        print(f"Info in {self.domain}: Finished")
        print(f"Info in {self.domain}: Found {len(profiles)} profiles.")
        for profile in profiles:
            db_session.add(profile)
            try:
                db_session.commit()
            except:
                db_session.rollback()

        logging.info(f"Info in {self.domain}: Finished!")

    def visit_full_profile_href(self, driver, profile, full_element_href):
        main_handle = driver.current_window_handle
        driver.switch_to.new_window("tab")
        driver.get(full_element_href)
        root = driver.find_element(By.XPATH, "/*")  # the root element

        profile.update_with_full_element(root)

        driver.close()
        driver.switch_to.window(main_handle)

    def skim_team_page(self, name_set, driver, profile_class):
        new_profiles = []
        preview_elements = driver.find_elements(
            By.CSS_SELECTOR, profile_class
        )  # we could also modify the page. just a thought. Also, does this have a limited size?
        logging.info(
            f"Info in {self.domain}: Found {len(preview_elements)} profile candidates."
        )
        for preview_element in preview_elements:
            profile = PersonalProfile(driver.current_url, self.firm_type, self.id)
            profile.update_with_preview_element(preview_element)

            if not profile.is_invalid:
                self.count += 1
                if self.count > self.limit:
                    return new_profiles
                text_nodes_text = profile.get_text_nodes_text(preview_element)
                profile.update_with_text_nodes(name_set, text_nodes_text)
                new_profiles.append(profile)

            driver.execute_script(
                "arguments[0].setAttribute('class', 'GARBAGE')", preview_element
            )  # HACK this is an important feature - it ensures that we don't re-parse old profiles, for websites that keep us on the same DOM with pagination.
        return new_profiles

    def get_next_if_exists(self, driver, page_index, exhausted):
        next_page_elements = driver.find_elements(By.TAG_NAME, "a")
        for next_page_element in next_page_elements:
            if next_page_element.text.lower() == "more":
                exhausted[0] = False
                print(next_page_element.text)
                driver.execute_script(
                    "arguments[0].click();", next_page_element
                )  # HACK this raw click seems to be more reliable than selenium's
                time.sleep(5)
                print("Clicked next!")
                print(f"Current url is: {driver.current_url}")
                return
            elif next_page_element.text.lower() == str(page_index[0] + 1):
                exhausted[0] = False
                print(next_page_element.text)
                driver.execute_script(
                    "arguments[0].click();", next_page_element
                )  # HACK this raw click seems to be more reliable than selenium's
                time.sleep(5)
                print("Clicked next!")
                print(f"Current url is: {driver.current_url}")
                page_index[0] += 1
                return


class PersonalProfile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    location = Column(String(100))
    firm_type = Column(Enum(FirmType))

    name = Column(String(100))
    is_key = Column(Boolean)

    emails = Column(String(1000), default=text(""))  # Can append multiple as required
    linkedins = Column(String(1000), default=text(""))
    others = Column(String(1000), default=text(""))

    is_invalid = Column(Boolean)

    job_id = Column(Integer, ForeignKey("jobs.id"))
    job = relationship("FirmJob", back_populates="profiles")

    def __init__(self, url, firm_type, job_id):
        self.location = url
        self.firm_type = firm_type
        self.job_id = job_id

        self.emails = ""
        self.linkedins = ""
        self.others = ""

    def add_email(self, email):
        logging.info("Entering email")
        if self.emails == "":
            self.emails = email
        else:
            self.emails += f";{email}"

    def add_linkedin(self, linkedin):
        logging.info("Entering linkedin")
        if self.linkedins == "":
            self.linkedins = linkedin
        else:
            self.linkedins += f";{linkedin}"

    def add_other_anchor(self, anchor):
        logging.info("Entering other anchor")
        if self.others == "":
            self.others = anchor
        else:
            self.others += f";{anchor}"

    def contains_email(self):
        return len(self.emails) > 0

    def is_likely_profile_preview(self, text_nodes, anchors):
        return not (
            len(anchors) > 10 or len(text_nodes) == 1 and text_nodes[0].text == ""
        )

    def get_text_nodes_text(self, preview_element):
        text_nodes = preview_element.find_elements(By.XPATH, "child::*")
        text_nodes_text = []
        for text_node in text_nodes:
            text = text_node.text
            text_nodes_text.append(text)
        return text_nodes_text

    def get_full_element_href(self):
        return parse.urljoin(self.location, self.others.split(";")[0])

    def update_with_preview_element(self, preview_element):  # NONETYPE + str ?
        logging.info(f"Entering preview update.")
        anchors = preview_element.find_elements(By.TAG_NAME, "a")
        text_nodes = preview_element.find_elements(By.XPATH, "child::*")
        if not self.is_likely_profile_preview:
            self.is_invalid = True
        for anchor in anchors:
            anchor_html = anchor.get_attribute("outerHTML").lower()
            href = anchor.get_dom_attribute("href")
            if href is not None:  # tentative for javascript, you get the idea
                if "linkedin" in href or "linked.in" in href:
                    self.add_linkedin(href)
                elif "mailto" in href:
                    self.add_email(href)
                    print(f"Got email: {href}")
                else:
                    self.add_other_anchor(href)

    def update_with_full_element(self, full_element):
        anchors = full_element.find_elements(By.TAG_NAME, "a")
        for anchor in anchors:
            anchor_html = anchor.get_attribute("outerHTML").lower()
            href = anchor.get_dom_attribute("href")
            if href is not None:  # tentative for javascript, you get the idea
                if "linkedin" in href or "linked.in" in href:
                    self.add_linkedin(href)
                elif "mailto" in href:
                    self.add_email(href)
                else:
                    self.add_other_anchor(href)

    def update_with_text_nodes(self, name_set, text_nodes_text):
        keylist = []
        if self.firm_type == FirmType.LAW:
            keylist += LAW_FIRM_KEY_PRACTICES
            keylist += LAW_FIRM_KEY_TITLES
        elif self.firm_type == FirmType.INVESTMENT:
            keylist += INVESTMENT_BANK_KEY_TITLES
        for text_node_text in text_nodes_text:
            for line in text_node_text.splitlines():
                namescore = 0
                tokens = line.split()
                if len(tokens) <= 5:
                    for token in tokens:
                        if token.lower() in name_set:
                            namescore += 1
                if namescore > 0.20 * len(tokens):
                    self.name = line
                    print(f"Likely name: {line}")
                if line.lower() in keylist:
                    self.is_key = True
                    print("Likely a key person.")
