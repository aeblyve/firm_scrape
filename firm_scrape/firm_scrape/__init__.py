import csv
import io
import logging
import re
import sqlite3
import time
from datetime import datetime
from urllib import parse
from datetime import timedelta
from functools import update_wrapper
from flask import Flask, g, render_template, request, make_response, current_app
from firm_scrape.models import FirmJob, FirmType, PersonalProfile
import flask_login
import requests
from firm_scrape.database import db_session
import firm_scrape.database
import flask
import undetected_chromedriver as uc  # present in the docker container
from flask import Flask, g, render_template, request
from flask_cors import CORS
from selenium.webdriver.common.by import By

from .util import crossdomain
from firm_scrape.database import db_session
from .constants import NAMES_FILE


app = Flask(__name__)
app.secret_key = "changemelater"

CORS(app)

users = {"user": {"password": "password"}}

login_manager = flask_login.LoginManager()
login_manager.init_app(app)


class User(flask_login.UserMixin):
    pass


@login_manager.user_loader
def user_loader(username):
    if username not in users:
        return

    user = User()
    user.id = username
    return user


@login_manager.request_loader
def request_loader(request):
    username = request.form.get("username")
    if username not in users:
        return

    user = User()
    user.id = username
    return user


@app.route("/login", methods=["GET", "POST"])
def login():
    if flask.request.method == "GET":
        return """
               <form action='login' method='POST'>
                <input type='text' name='username' id='username' placeholder='username'/>
                <input type='password' name='password' id='password' placeholder='password'/>
                <input type='submit' name='submit'/>
               </form>
               """

    username = flask.request.form["username"]
    if (
        username in users
        and flask.request.form["password"] == users[username]["password"]
    ):
        user = User()
        user.id = username
        flask_login.login_user(user)
        return flask.redirect(flask.url_for("landing_view"))

    return "Bad login"


@app.route("/logout")
def logout():
    flask_login.logout_user()
    return "Logged out"


@login_manager.unauthorized_handler
def unauthorized_handler():
    return "Unauthorized", 401


@app.route("/jobs/<id>", methods=["GET"])
def download_job_report(id):
    con = sqlite3.connect("/database.db")

    outfile = io.StringIO("", newline="")
    outcsv = csv.writer(outfile)

    cursor = con.execute("select * from jobs where id = ?", (id,))
    outcsv.writerows(cursor.fetchall())

    cursor = con.execute("select * from profiles where job_id = ?", (id,))
    outcsv.writerows(cursor.fetchall())

    outfile.flush()
    outfile.seek(0)
    resp = flask.Response(outfile.read())
    resp.headers[
        "Content-Disposition"
    ] = f'attachment; filename="firm_scrape-job-{str(id)}.csv"'
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"

    return resp


@app.route("/jobs/<id>/emails", methods=["GET"])
def download_all_job_emails(id):
    con = sqlite3.connect("/database.db")

    outfile = io.StringIO("", newline="")
    outcsv = csv.writer(outfile)

    cursor = con.execute("select emails from profiles where job_id = ?", (id,))

    empty = True

    for emails in cursor.fetchall():
        if emails[0]:
            for email in emails[0].split(";"):
                outcsv.writerow([email])
                empty = False
    if empty:
        outcsv.writerow(["No emails found!"])

    outfile.flush()
    outfile.seek(0)
    resp = flask.Response(outfile.read())
    resp.headers[
        "Content-Disposition"
    ] = f'attachment; filename="firm_scrape-job-{str(id)}-emails.csv"'
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"

    return resp


@app.route("/jobs", methods=["GET"])
@flask_login.login_required
def view_all_jobs():
    context = {"jobs": FirmJob.query.all()}
    print(context)
    return render_template("jobs.html", **context)


@app.route("/", methods=["GET"])
def landing_view():
    return render_template("index.html")


@app.route("/add", methods=["GET", "POST", "OPTIONS"])
@crossdomain(origin="*")
@flask_login.login_required
def add_view():
    if request.method == "POST":
        content = request.json
        start_time = datetime.now()
        for domain_list, firm_type, limit in content["jobs"]:
            limit = float("inf") if limit == "" else int(limit)
            for domain in domain_list.splitlines():
                firm_job = FirmJob(domain, FirmType[firm_type.upper()], limit)
                db_session.add(firm_job)
                try:
                    db_session.commit()
                except:
                    db_session.rollback()

        for firm_job in FirmJob.query.all():
            firm_job.execute(name_set)
            db_session.commit()

        csv_file_text = db2csv()

        resp = flask.Response(csv_file_text)
        resp.headers["Content-Disposition"] = f"attachment; {str(start_time)}.csv"
        return resp
    else:
        return render_template("add.html")


def db2csv():
    con = sqlite3.connect("/database.db")
    outfile = io.StringIO("", newline="")
    outcsv = csv.writer(outfile)

    cursor = con.execute("select * from profiles")

    outcsv.writerows(cursor.fetchall())

    cursor = con.execute("select * from jobs")

    outcsv.writerows(cursor.fetchall())

    outfile.flush()
    outfile.seek(0)
    return outfile.read()


@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()


@app.before_first_request
def setup_nameset():
    global name_set
    name_set = set()
    print("Initializing nameset.")
    with open(NAMES_FILE, "r") as f:
        for line in f.read().splitlines():
            name_set.add(line)
    print("Finished initializing nameset.")


@app.before_first_request
def setup_logging():
    logging.basicConfig(level=20)


@app.before_first_request
def init_db():
    firm_scrape.database.init_db()
