from datetime import timedelta
from functools import update_wrapper
import logging
from flask import Flask, g, render_template, request, make_response, current_app
import undetected_chromedriver as uc  # present in the docker container
import re
from .constants import NAME_LIMIT
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement


def crossdomain(
    origin=None,
    methods=None,
    headers=None,
    max_age=21600,
    attach_to_all=True,
    automatic_options=True,
):
    """Decorator function that allows crossdomain requests.
    Courtesy of
    https://blog.skyred.fi/articles/better-crossdomain-snippet-for-flask.html
    """
    if methods is not None:
        methods = ", ".join(sorted(x.upper() for x in methods))
    # use str instead of basestring if using Python 3.x
    if headers is not None and not isinstance(headers, str):
        headers = ", ".join(x.upper() for x in headers)
    # use str instead of basestring if using Python 3.x
    if not isinstance(origin, str):
        origin = ", ".join(origin)
    if isinstance(max_age, timedelta):
        max_age = max_age.total_seconds()

    def get_methods():
        """Determines which methods are allowed"""
        if methods is not None:
            return methods

        options_resp = current_app.make_default_options_response()
        return options_resp.headers["allow"]

    def decorator(f):
        """The decorator function"""

        def wrapped_function(*args, **kwargs):
            """Caries out the actual cross domain code"""
            if automatic_options and request.method == "OPTIONS":
                resp = current_app.make_default_options_response()
            else:
                resp = make_response(f(*args, **kwargs))
            if not attach_to_all and request.method != "OPTIONS":
                return resp

            h = resp.headers
            h["Access-Control-Allow-Origin"] = origin
            h["Access-Control-Allow-Methods"] = get_methods()
            h["Access-Control-Max-Age"] = str(max_age)
            h["Access-Control-Allow-Credentials"] = "true"
            h[
                "Access-Control-Allow-Headers"
            ] = "Origin, X-Requested-With, Content-Type, Accept, Authorization"
            if headers is not None:
                h["Access-Control-Allow-Headers"] = headers
            return resp

        f.provide_automatic_options = False
        return update_wrapper(wrapped_function, f)

    return decorator


def setup_webdriver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.arguments.extend(
        [
            "--disable-extensions",
            "--disable-application-cache",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ]
    )
    driver = uc.Chrome(options, version_main=111)
    driver.set_page_load_timeout(20)
    return driver


def get_profile_selector(driver, name_set):
    name_elements = get_name_elements(driver, name_set)

    name_selector = ""
    name_selector_shadow = ""
    name_1 = None
    name_2 = None
    name_1_parent = None
    name_2_parent = None

    for i in range(0, len(name_elements) - 1):
        name_1 = name_elements[i]
        name_2 = name_elements[i + 1]
        name_selector = f"{name_1.tag_name}"
        name_selector_shadow = ""

        name_1_parent = name_1.find_element(By.XPATH, "./..")
        name_2_parent = name_2.find_element(By.XPATH, "./..")

        try:
            while (
                name_1_parent != name_2_parent
            ):  # uses elementid under the hood ssssss
                name_1 = name_1.find_element(By.XPATH, "./..")
                name_2 = name_2.find_element(By.XPATH, "./..")
                name_1_parent = name_1_parent.find_element(By.XPATH, "./..")
                name_2_parent = name_2_parent.find_element(By.XPATH, "./..")
                name_selector_shadow = name_selector
                name_selector = f"{name_1.tag_name} > " + name_selector
        except:
            logging.info(f"Info: Failed - trying again with next name window")
            continue
        break

    if name_1 is None or name_2 is None:
        raise Exception(f"Not enough names to gain structure.")

    common = return_token_intersection(
        name_1.get_attribute("class").split(), name_2.get_attribute("class").split()
    )

    common = css_classtokens2selector(common)

    if common != ".":
        # drill down to avoid weirdness like an unbalanced DOM tree hierarchy
        count = len(name_1.find_elements(By.TAG_NAME, "a"))
        name_1_children = name_1.find_elements(By.XPATH, "child::*")
        if len(name_1_children) != 1:
            return css_classtokens2selector(name_1.get_attribute("class").split())
        name_1_child = name_1_children[0]
        child_count = len(name_1_child.find_elements(By.TAG_NAME, "a"))
        while count == child_count:
            name_1 = name_1_child
            count = child_count
            name_1_children = name_1.find_elements(By.XPATH, "child::*")
            if len(name_1_children) != 1:
                return css_classtokens2selector(name_1.get_attribute("class").split())
            name_1_child = name_1_children[0]
            child_count = len(name_1_child.find_elements(By.TAG_NAME, "a"))

        return common

    else:
        # throw out common, it is empty
        profile_selector = f"{name_1.tag_name}"

        # traverse upwards until we hit the root
        while True:
            try:
                name_1 = name_1.find_element(By.XPATH, "./..")
                profile_selector = f"{name_1.tag_name} > " + profile_selector
            except:
                break
        print(profile_selector)
        name_selector = profile_selector + " > " + name_selector_shadow
        print(name_selector)

        logging.info(f"Info: Derived profile selector {profile_selector}")

        return profile_selector


def return_token_intersection(tokens1, tokens2):
    tokens1_set = set()
    tokens2_set = set()
    for token in tokens1:
        tokens1_set.add(token)
    for token in tokens2:
        tokens2_set.add(token)
    return list(tokens1_set.intersection(tokens2_set))


def css_classtokens2selector(classtokens):
    selector = str.join(".", classtokens)
    return "." + selector


def get_name_elements(driver, name_set):
    elements = driver.find_elements(
        By.XPATH, "//*[text()]"
    )  # TODO this query is inefficient
    name_regex = re.compile(r"^[a-zA-Z]+( [A-Z] | )[a-zA-Z]+$")
    names = []
    names_backing_set = set()
    for element in elements:
        test = re.sub(r"\.|,", "", element.text)
        print(test)
        match_huh = name_regex.match(test)
        if match_huh:
            match = match_huh.group(0)
            if is_name(match, name_set):
                print(f"{match} found in name dictionary")
                if element.text not in names_backing_set:
                    names.append(element)
                    names_backing_set.add(element.text)
                    if len(names) > NAME_LIMIT:
                        print(f"Found {NAME_LIMIT} names, exiting namesearch.")
                        break
    for name in names:
        print(name.text)
    return list(names)


def is_name(pot_name: str, name_set) -> bool:
    count = 0
    for token in pot_name.lower().split():
        if token in name_set:
            count += 1
    return count >= 2
