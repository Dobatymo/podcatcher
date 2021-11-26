from __future__ import annotations

import logging
import os
from argparse import ArgumentParser
from typing import Tuple

from flask import Flask, Response, abort, flash, make_response, redirect, render_template, request, send_file, url_for
from genutility.args import is_dir
from genutility.flask import Base64Converter
from wtforms import Form, IntegerField, StringField, validators

from catcher import Catcher, InvalidFeed
from streaming import YoutubeToFeed

"""
needs
"""

"""
https://validator.w3.org/feed/docs/rss2.html

channel, required: title, link, description
item, required: title or description
only category is allowed to occur multiple times (but enclosure commonly occurs multiple times as well)
enclosure must have: length, type, url

item id order: guid, link, title, description, pubDate

"""

""" TODO
- analyse file duration for downloaded files and save to db
- save relative local path to db

"""

app = Flask(__name__)
app.secret_key = os.urandom(24)

from utils import DEFAULT_APPDATA_DIR

parser = ArgumentParser()
parser.add_argument("--appdata-dir", type=is_dir, default=DEFAULT_APPDATA_DIR, help="Path to appdata directory")
parser.add_argument("--quiet", action="store_true", help="don't show debug output")
args = parser.parse_args()

if args.quiet:
    logging.basicConfig(level=logging.INFO)
else:
    logging.basicConfig(level=logging.DEBUG)

c = Catcher(args.appdata_dir)
yt = YoutubeToFeed()

try:
    c.load_local()
except FileNotFoundError:
    c.update_feeds()
    c.save_local()

app.url_map.converters["binary"] = Base64Converter


@app.errorhandler(404)
def page_not_found(e):
    logging.info(f"404: {request.url}")
    return ("Not Found", 404)


def is_downloaded(info):
    # type: (dict, ) -> bool

    return info.get("localname") is not None


def splitonce(string, delim):
    # type: (str, str) -> Tuple[str, str]

    a, b = string.split(delim)
    return a, b


def redirect_to_cast():
    if request.referrer:  # is valid url test missing
        return redirect(request.referrer)
    else:
        return redirect(url_for("casts"))


@app.route("/", methods=["GET"])
@app.route("/cast/<binary:cast_uid>", methods=["GET"])
def casts(cast_uid=None):

    # cast_uid == cast_title

    # fixme: there can be episodes in "all" which are from casts not in the cast list

    episodes = list()

    if cast_uid is None:
        for cast_uid, feed in c.db.items():
            for episode_uid in feed["items"]:
                info = c.episode(cast_uid, episode_uid)
                episodes.append((cast_uid, episode_uid, cast_uid, info["title"], info, is_downloaded(info)))
        cast_title = "All"
    else:
        try:
            feed = c.db[cast_uid]
        except KeyError:
            flash("Invalid Cast", "error")
            return redirect(url_for("casts"))

        for episode_uid in feed["items"]:
            info = c.episode(cast_uid, episode_uid)
            episodes.append((cast_uid, episode_uid, cast_uid, info["title"], info, is_downloaded(info)))

        cast_title = cast_uid

    casts = list()
    for cast_uid, info in c.casts.items():
        casts.append((cast_uid, cast_uid, info["url"]))

    if c.config["descending"]:
        episodes = sorted(episodes, key=lambda x: x[4]["date"], reverse=True)
    else:
        episodes = sorted(episodes, key=lambda x: x[4]["date"], reverse=False)
    return render_template("casts.html", cast_title=cast_title, casts=casts, episodes=episodes)


@app.route("/save", methods=["GET"])
def save():
    c.save_roaming()
    c.save_local()
    return redirect(url_for("casts"))


@app.route("/massedit", methods=["POST"])
def massedit():

    episodes = request.form.getlist("episode")
    action = request.form.get("action")

    if episodes:

        episodes = list(splitonce(e, "|") for e in episodes)  # which exception here?

        if action == "delete":
            for cast_uid, episode_uid in episodes:
                c.remove_episode(cast_uid, episode_uid)
            flash("Removed episodes", "info")
        elif action == "download":
            for cast_uid, episode_uid in episodes:
                c.download_item(cast_uid, episode_uid)
            flash("Downloaded episodes", "info")
        elif action == "play":
            flash("Unsupported", "warning")
        elif action == "listened":
            for cast_uid, episode_uid in episodes:
                c.listenedto(cast_uid, episode_uid)
            flash("Marked episodes as listened to", "info")
        else:
            flash("Invalid operation", "error")
    else:
        flash("No episodes selected", "info")
    return redirect_to_cast()


@app.route("/removeepisode/<binary:cast_uid>/<binary:episode_uid>", methods=["GET"])
def removeepisode(cast_uid, episode_uid):
    localname = c.remove_episode(cast_uid, episode_uid)
    if localname:
        flash(f"Removed episode: {cast_uid}/{localname}", "info")
    else:
        flash("Invalid episode", "error")
    return redirect_to_cast()


@app.route("/downloadepisode/<binary:cast_uid>/<binary:episode_uid>", methods=["GET"])
def downloadepisode(cast_uid, episode_uid):
    ep = c.download_item(cast_uid, episode_uid)  # is async now
    flash("Started downloading episode: {} / {}".format(cast_uid, ep["title"]), "info")
    """
	if is_downloaded(ep):
		flash("Downloaded episode: {} / {}".format(cast_uid, ep["title"]), "info")
	else:
		flash("Downloading episode {} / {} failed.".format(cast_uid, ep["title"]), "warning")
	"""
    return redirect_to_cast()


@app.route("/playepisode/<binary:cast_uid>/<binary:episode_uid>", methods=["GET"])
def playepisode(cast_uid, episode_uid):
    info = c.episode(cast_uid, episode_uid)
    if info:
        filename = info["localname"]
        try:
            sf = send_file(
                os.path.join(c.casts_dir, cast_uid, info["localname"]),
                attachment_filename=filename,
                mimetype=info["mimetype"],
                conditional=True,
                last_modified=info["date"],
            )
        except FileNotFoundError:
            abort(404)
        response = make_response(sf)
        response.headers["Content-Disposition"] = 'inline; filename="{}"'.format(
            filename
        )  # flask doesn't support inline filename. encoding?
        return response
    else:
        flash("Invalid episode", "error")
        return redirect_to_cast()


@app.route("/listento/<binary:cast_uid>/<binary:episode_uid>", methods=["GET"])
def listento(cast_uid, episode_uid):
    try:
        c.listenedto(cast_uid, episode_uid)
    except KeyError:
        flash("Cast does not exist", "error")
    return redirect_to_cast()


@app.route("/unhear/<binary:cast_uid>/<binary:episode_uid>", methods=["GET"])
def unhear(cast_uid, episode_uid):
    try:
        c.forget_episode(cast_uid, episode_uid)
    except KeyError:
        flash("Cast does not exist", "error")
    return redirect_to_cast()


@app.route("/addcastc", methods=["POST"])
def addcastc():
    url = request.form.get("url")
    if url.startswith("https://www.youtube.com/playlist"):
        url = url_for("youtube_to_feed", format="rss", url=url, _external=True)
    try:
        title, feed = c.get_feed(url)
    except InvalidFeed:
        flash("Parsing feed failed", "warning")
        return redirect(url_for("casts"))

    return render_template("addcast.html", title=title, url=url)


@app.route("/addcast", methods=["POST"])
def addcast():
    title = request.form.get("title")
    url = request.form.get("url")
    __, feed = c.get_feed(url)
    if not c.add_feed(url, title, feed):
        flash("Directory exists already", "warning")
    c.save_local()
    flash(f"Added {title}", "info")
    return redirect(url_for("casts"))


@app.route("/removecast/<binary:cast_uid>", methods=["GET"])
def removecast(cast_uid):
    c.remove_cast(cast_uid)
    flash(f"Deleted {cast_uid}", "info")
    return redirect(url_for("casts"))


@app.route("/renamecastc/<binary:cast_uid>", methods=["GET"])
def renamecastc(cast_uid):
    return render_template("renamecast.html", cast_uid=cast_uid, title=cast_uid)


@app.route("/renamecast/<binary:cast_uid>", methods=["POST"])
def renamecast(cast_uid):
    name = request.form.get("title")
    if name:
        try:
            c.rename_cast(cast_uid, name)
        except KeyError:
            flash("Cast does not exist", "error")
        except ValueError as e:
            flash(str(e), "error")
    else:
        flash("Name missing", "error")
    return redirect(url_for("casts"))


@app.route("/action/refresh", methods=["GET"])
def refresh():
    c.update_feeds()
    c.save_local()
    return redirect(url_for("casts"))


@app.route("/refresher", methods=["GET"])
def refresher():
    c.update_feeds()
    c.save_local()
    return render_template("refresher.html", interval=c.interval)


@app.route("/action/download", methods=["GET"])
def download():
    c.download_items()
    return redirect(url_for("casts"))


"""
{"casts-directory": "./Podcasts", "user-agent": "gPodder/2.13 (+http://gpodder.org/)", "descending": true, "network-timeout": 10, "refresh-interval": 3600}
"""


class ConfigForm(Form):

    # BAD WAY TO DEFINE DEFAULT VALUES
    # 1. globals are needed
    # 2. values remain old values if changed later

    casts_directory = StringField(
        "Casts directory",
        description="The path where downloaded casts are saved",
        validators=[validators.input_required()],
    )
    user_agent = StringField(
        "User agent", description="User agent HTTP header", validators=[validators.input_required()]
    )
    network_timeout = IntegerField(
        "Network timeout", description="Network timeout in seconds", validators=[validators.input_required()]
    )
    refresh_interval = IntegerField(
        "Refresh interval", description="Refresh interval of feeds in seconds", validators=[validators.input_required()]
    )


@app.route("/config", methods=["GET", "POST"])
def config():

    if request.method == "POST":
        form = ConfigForm(formdata=request.form)
        if form.validate():
            flash("Config changed and saved")
            c.config["casts-directory"] = form.casts_directory.data
            c.config["user-agent"] = form.user_agent.data
            c.config["network-timeout"] = form.network_timeout.data
            c.config["refresh-interval"] = form.refresh_interval.data
            c.save_config()
    else:
        config = {k.replace("-", "_"): v for k, v in c.config.items()}
        print(config)
        form = ConfigForm(**config)
    return render_template("config.html", form=form)


@app.route("/status", methods=["GET"])
def status():
    try:
        interval = int(request.args.get("interval"))
    except (TypeError, ValueError):
        interval = 2

    queued, active, completed, failed = c.get_download_status()
    return render_template(
        "status.html", interval=interval, queued=queued, active=active, completed=completed, failed=failed
    )


@app.route("/youtube/<format>/<path:url>", methods=["GET"])
def youtube_to_feed(format, url):
    formats = {"rss": ("rss_str", "application/rss+xml"), "atom": ("atom_str", "application/atom+xml")}

    if format not in formats:
        return ("Invalid format", 400)
    try:
        feed = yt.get_feed(url)
    except ValueError:
        logging.exception("Invalid playlist")
        return (f"Invalid playlist url: {url}", 400)
    yt.save_cache()

    format_str = formats[format][0]
    return Response(getattr(feed, format_str)(pretty=True), mimetype=formats[format][1])


if __name__ == "__main__":

    # import atexit

    # atexit.register(save_data)

    app.run(host="127.0.0.1", port=8000, debug=True, threaded=True, use_reloader=False)  # nosec
