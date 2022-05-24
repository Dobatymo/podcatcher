import concurrent.futures
import email.utils
import logging
import mimetypes
import os
import os.path
import re
import socket
import ssl
import sys
import time
from datetime import datetime, timedelta
from functools import partial
from http.client import InvalidURL
from io import BytesIO
from pathlib import Path
from typing import IO, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError

import certifi
import feedparser
from feedparser import FeedParserDict
from genutility.concurrency import ProgressThreadPool
from genutility.datetime import now
from genutility.filesystem import safe_filename
from genutility.func import retry
from genutility.http import ContentInvalidLength, URLRequest
from genutility.iter import first_not_none
from genutility.json import BuiltinRoundtripDecoder, BuiltinRoundtripEncoder, read_json, write_json
from genutility.string import toint

logger = logging.getLogger(__name__)

"""

Failed
None (URL can't contain control characters. '/podlove/file/992/s/feed/c/nukularaacfeed/Radio Nukular X Babbel-Net Folge 1.m4a' (found at least ' '))
None (URL can't contain control characters. '/podlove/file/1278/s/feed/c/nukularaacfeed/Rockstah hat etwas vor.m4a' (found at least ' '))
None (HTTP Error 404: Media File not found)

"""

DEFAULT_NETWORK_TIMEOUT = 60
DEFAULT_CONCURRENT_DOWNLOADS = 2
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.45 Safari/537.36"
)

ssl_context = ssl.create_default_context(cadata=certifi.contents())


class ProgressReport:
    def __init__(self, file: IO[str] = sys.stdout) -> None:

        self.file = file
        self.start = time.time()

    def __call__(self, done, total):
        percent = done * 100 / total
        time_diff = time.time() - self.start
        total_mb = total / 1024 / 1024
        print(
            f"Downloaded {percent:05.2f}% in {time_diff:.0f}s ({total_mb:05.2f}mb)",
            end="\r",
            file=self.file,
        )


"""
class DownloadTask:

	def __init__(self):
		# identification
		cast_uid, episode_uid
		# info needed for download
		url, basepath, filename, fn_prio, overwrite, timeout, headers
		# info obtained after download
		status, length, localname
"""


def download(
    url: str,
    basepath: str,
    filename: Optional[str] = None,
    fn_prio=None,
    overwrite: bool = False,
    suffix=".partial",
    report=None,
    timeout=5 * 60,
    headers=None,
) -> Tuple[Optional[int], str]:

    return URLRequest(url, headers, timeout, ssl_context).download(
        basepath, filename, fn_prio, overwrite, suffix, report
    )


def download_handle(
    report,
    setter: Callable,
    url: str,
    basepath: str,
    filename: Optional[str],
    fn_prio,
    overwrite: bool,
    expected_size=None,
    timeout=None,
    headers=None,
):

    localname: Optional[str]
    status: Optional[Exception]

    try:
        (length, localname) = download(
            url, basepath, filename, fn_prio, overwrite, report=report, timeout=timeout, headers=headers
        )
        status = None

        if expected_size and expected_size != length:
            logging.error("Download succeeded, but size %s doesn't match enclosure length %s", length, expected_size)

    except FileExistsError as e:
        localname = os.path.basename(e.filename)
        length = os.stat(e.filename).st_size  # can raise not found or sth. again
        status = None

        if expected_size and expected_size != length:
            logging.warning(
                "%s exists, but size %s doesn't match enclosure length %s", e.filename, length, expected_size
            )

    except ContentInvalidLength as e:
        localname = os.path.basename(e.args[0])
        length = e.args[2]  # can raise not found or sth. again
        status = e
        logging.warning("%s might be incomplete. Transferred %d/%d", localname, e.args[1], e.args[2])
    except HTTPError as e:
        localname = None
        length = None
        status = e
        if e.code == 404:
            logging.error("HTTP 404 error for <%s>", url)
        else:
            logging.exception("Downloading <%s> failed.", url)
    except (URLError, InvalidURL) as e:
        localname = None
        length = None
        status = e
        logging.exception("Downloading <%s> failed.", url)
    except Exception as e:
        localname = None
        length = None
        status = e
        logging.exception("Downloading <%s> failed.", url)

    return (setter, status, localname, length)  # put some info there to make sure failed downloads can be repeated


class NoTitleError(Exception):
    pass


class InvalidFeed(Exception):
    pass


durationp = re.compile(r"(?:([0-9]{1,2}):)?([0-9]{1,2}):([0-9]{1,2})")


def parse_itunes_duration(itunes_duration: Optional[str]) -> Optional[timedelta]:
    if itunes_duration is None:
        return None

    m = durationp.match(itunes_duration)
    if m:
        hours, minutes, seconds = m.groups()
        hrs = toint(hours, 0)
        min = int(minutes)
        sec = int(seconds)
        return timedelta(hours=hrs, minutes=min, seconds=sec)

    try:
        sec = int(itunes_duration)
    except ValueError:
        return None
    else:
        return timedelta(seconds=sec)


class Catcher:

    FILENAME_CONFIG = "config.json"
    FILENAME_CASTS = "casts.json"
    FILENAME_FEEDS = "feeds.db.json"

    def __init__(self, appdatadir: Path) -> None:

        self.appdatadir = appdatadir

        self.load_config()

        self.timeout = self.config.get("network-timeout", DEFAULT_NETWORK_TIMEOUT)
        self.user_agent = self.config.get("user-agent", DEFAULT_USER_AGENT)
        self.casts_dir = Path(self.config["casts-directory"])
        self.interval = self.config["refresh-interval"]  # seconds, unused so far
        self.concurrent_downloads = self.config.get("concurrent-downloads", DEFAULT_CONCURRENT_DOWNLOADS)

        self.headers = {"User-Agent": self.user_agent}

        self.dl = ProgressThreadPool(concurrent=self.concurrent_downloads)

        self.load_roaming()
        # self.load_local()

    def load_config(self) -> None:

        self.config = read_json(self.appdatadir / self.FILENAME_CONFIG, cls=BuiltinRoundtripDecoder)

    def save_config(self) -> None:

        write_json(
            self.config, self.appdatadir / self.FILENAME_CONFIG, indent="\t", cls=BuiltinRoundtripEncoder, safe=True
        )

    def load_roaming(self) -> None:

        self.casts = read_json(self.appdatadir / self.FILENAME_CASTS, cls=BuiltinRoundtripDecoder)

    def save_roaming(self) -> None:

        if len(self.casts) != len(self.db):
            logging.warning("Inconsistent file information. Casts: %s, DB: %s", len(self.casts), len(self.db))

        write_json(
            self.casts, self.appdatadir / self.FILENAME_CASTS, indent="\t", cls=BuiltinRoundtripEncoder, safe=True
        )

    def load_local(self) -> None:

        self.db = read_json(self.appdatadir / self.FILENAME_FEEDS, cls=BuiltinRoundtripDecoder)

    def save_local(self) -> None:

        if len(self.casts) != len(self.db):
            logging.warning("Inconsistent file information. Casts: %s, DB: %s", len(self.casts), len(self.db))

        write_json(self.db, self.appdatadir / self.FILENAME_FEEDS, indent="\t", cls=BuiltinRoundtripEncoder, safe=True)

    def episode(self, cast_uid: str, episode_uid: str) -> Optional[dict]:

        # if "|" in cast_uid or "|" in episode_uid:
        # 	raise ValueError("arguments cannot contain '|'")

        cast = self.db.get(cast_uid)
        if cast:
            return cast["items"].get(episode_uid)
        return None

    def listenedto(self, cast_uid: str, episode_uid: str, date: Optional[datetime] = None) -> datetime:

        if not date:
            date = now()
        info = self.episode(cast_uid, episode_uid)
        if not info:
            raise KeyError((cast_uid, episode_uid))
        info["listened"] = date
        return date

    def forget_episode(self, cast_uid: str, episode_uid: str) -> datetime:

        info = self.episode(cast_uid, episode_uid)
        if not info:
            raise KeyError((cast_uid, episode_uid))
        return info.pop("listened")

    def get_feed(self, url: str) -> Tuple[str, FeedParserDict]:

        r = URLRequest(url, context=ssl_context)
        data = BytesIO(r.load())
        feed = feedparser.parse(
            data,
            response_headers={
                "Content-Location": url,
                "content-type": r.headers["content-type"],
            },
        )

        if feed.bozo:
            logging.error("Feed mal-formed <%s>: %s", url, feed.bozo_exception)

        if len(feed.feed) == 0 and len(feed.entries) == 0:  # compare with standard
            raise InvalidFeed("Feed does neither contain a description nor files")

        title = feed.feed.get("title")

        return (title, feed)

    def update_feed_url(self, cast_uid: str, url: str):
        try:
            feed = self.casts[cast_uid]
        except KeyError:
            raise ValueError(f"Feed {cast_uid} doesn't exist.") from None

        feed["url"] = url

    def add_feed(self, url: str, cast_uid: str, feed: FeedParserDict) -> bool:

        if not url or not cast_uid or not feed:
            raise ValueError("argument values cannot be empty")

        if cast_uid in self.casts:
            raise ValueError(f"{cast_uid} already exists")

        cast_uid_safe = safe_filename(cast_uid, "_")
        collision = self.is_name_collision_add(cast_uid_safe)
        if collision:
            raise ValueError(f"Name collision with {collision}")

        self.casts[cast_uid] = {"url": url}
        self.update_feed(cast_uid, feed)
        self.save_roaming()
        self.save_local()

        try:
            (self.casts_dir / cast_uid_safe).mkdir(exist_ok=True)
            return True
        except FileExistsError:
            return False
        except FileNotFoundError as e:
            logger.warning("Could not create directory: %s", e)
            return False

    def remove_cast(self, cast_uid: str, files: bool = False) -> None:

        if (cast_uid in self.casts) != (cast_uid in self.db):
            raise RuntimeError("Inconsistent database")

        if files:
            raise RuntimeError("Deleting files not yet implemented")

        del self.casts[cast_uid]
        del self.db[cast_uid]  # should 'listened to' information be kept?
        self.save_roaming()
        self.save_local()

    def is_name_collision_add(self, cast_uid_new_safe: str) -> Optional[str]:

        for cast_uid in self.casts:
            if cast_uid_new_safe == safe_filename(cast_uid, "_"):
                return cast_uid

        return None

    def is_name_collision_rename(self, cast_uid_new_safe: str, cast_uid_old: str) -> Optional[str]:

        for cast_uid in self.casts:
            if cast_uid_old != cast_uid:
                if cast_uid_new_safe == safe_filename(cast_uid, "_"):
                    return cast_uid

        return None

    def rename_cast(self, cast_uid_old: str, cast_uid_new: str) -> None:

        if (cast_uid_new in self.casts) != (cast_uid_new in self.db):
            raise RuntimeError("Inconsistent database")

        if (cast_uid_old in self.casts) != (cast_uid_old in self.db):
            raise RuntimeError("Inconsistent database")

        if cast_uid_old == cast_uid_new:  # put after asserts, so inconsistent database is found earlier
            return

        if cast_uid_new in self.casts:
            raise ValueError("Cast already exists")

        cast_uid_old_safe = safe_filename(cast_uid_old, "_")
        cast_uid_new_safe = safe_filename(cast_uid_new, "_")

        collision = self.is_name_collision_rename(cast_uid_new_safe, cast_uid_old)
        if collision:
            raise ValueError(f"Name collision with {collision}")

        try:
            (self.casts_dir / cast_uid_old_safe).rename(self.casts_dir / cast_uid_new_safe)
        except FileNotFoundError:
            raise ValueError("Cast directory not found")
        except FileExistsError:
            raise ValueError("Directory already exists")

        self.casts[cast_uid_new] = self.casts.pop(cast_uid_old)
        self.db[cast_uid_new] = self.db.pop(cast_uid_old)

    def remove_episode(self, cast_uid: str, episode_uid: str, file: bool = False) -> Optional[str]:

        if file:
            raise RuntimeError("Deleting files not yet implemented")

        ep = self.episode(cast_uid, episode_uid)
        if not ep:
            raise KeyError((cast_uid, episode_uid))

        return ep.pop("localname", None)

    def update_feed(self, cast_uid: str, feed: FeedParserDict) -> None:

        try:
            pub: Optional[datetime] = email.utils.parsedate_to_datetime(feed.feed.published)
        except AttributeError:
            pub = None

        try:
            self.db[cast_uid]["date"] = pub
        except KeyError:
            self.db[cast_uid] = dict()
            self.db[cast_uid]["items"] = dict()
            self.db[cast_uid]["date"] = pub

        for entry in feed.entries:
            try:
                db_entry = self.db[cast_uid]["items"][self.get_episode_uid(entry)]
            except KeyError:
                episode_uid = self.get_episode_uid(entry)
                self.db[cast_uid]["items"][episode_uid] = dict()
                db_entry = self.db[cast_uid]["items"][episode_uid]

            try:
                entry_pub: Optional[datetime] = email.utils.parsedate_to_datetime(entry.published)
            except AttributeError:
                entry_pub = None

            duration = parse_itunes_duration(entry.get("itunes_duration"))
            db_entry.update(
                {
                    "title": entry.get("title"),
                    "date": entry_pub,
                    "duration": duration,
                    "description": entry.get("description"),
                }
            )

            encs = len(entry.get("enclosures"))
            if encs == 0:
                db_entry.update({"href": None, "length": None, "mimetype": None})
            elif encs == 1:
                enclosure = entry.enclosures[0]
                db_entry.update(
                    {
                        "href": enclosure.get("href"),
                        "length": toint(enclosure.get("length")),
                        "mimetype": enclosure.get("type"),
                    }
                )
            else:
                raise InvalidFeed("Feed contains multiple enclosures")

    def update_feeds(self) -> None:
        feed: FeedParserDict

        logging.debug("Refreshing all feeds")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures: Dict[concurrent.futures.Future, str] = {}
            for cast_uid, cast in self.casts.items():
                future = executor.submit(
                    retry,
                    partial(self.get_feed, cast["url"]),
                    10,
                    (ConnectionError, URLError, socket.timeout, ContentInvalidLength),
                    attempts=2,
                    multiplier=1.5,
                )
                futures[future] = cast_uid

            for future in concurrent.futures.as_completed(futures):
                cast_uid = futures[future]
                cast = self.casts[cast_uid]
                try:
                    _title, feed = future.result()
                    self.update_feed(cast_uid, feed)
                except (ConnectionError, URLError, socket.timeout, ContentInvalidLength) as e:
                    logging.warning("Could not update %s <%s>: %s", cast_uid, cast["url"], e)
                except InvalidFeed as e:
                    logging.warning("Invalid feed %s <%s>: %s", cast_uid, cast["url"], e)

        self.save_local()

    def get_episode_uid(self, item: dict) -> Optional[str]:

        return first_not_none([item.get("guid"), item.get("link"), item.get("title"), item.get("description")])

    def get_download_status(self) -> Tuple[list, list, list, list]:

        waiting = list(
            (url, basepath, filename, expected_size)
            for callable, (setter, url, basepath, filename, fn_prio, overwrite), (
                expected_size,
                timeout,
                headers,
            ) in self.dl.get_waiting()
        )
        running = list(
            ((url, basepath, filename, expected_size), done, total)
            for (
                callable,
                (setter, url, basepath, filename, fn_prio, overwrite),
                (expected_size, timeout, headers),
            ), done, total in self.dl.get_running()
        )
        completed = self.dl.get_completed()
        failed = self.dl.get_failed()
        return waiting, running, completed, failed

    def download_item(
        self, cast_uid: str, episode_uid: str, force: bool = False, overwrite: bool = False
    ) -> Optional[dict]:

        # changes self.db

        fn_prio = self.casts[cast_uid].get("filename", None)
        if fn_prio:
            fn_prio = (fn_prio,)
        else:
            fn_prio = None

        dirpath = self.casts_dir / safe_filename(cast_uid, "_")  # make sure still unique

        db_entry = self.episode(cast_uid, episode_uid)

        if not db_entry:
            raise KeyError((cast_uid, episode_uid))

        if not force and db_entry.get("localname"):
            logging.debug("File already downloaded for %s/%s: %s", cast_uid, episode_uid, db_entry.get("localname"))
            return None

        # these two values are only given own variables to aid mypy in its flow analysis
        title = db_entry.get("title")
        mimetype = db_entry.get("mimetype")

        if not title or not mimetype:
            filename = None
        else:
            ext = {"audio/x-mpeg": ".mp3"}.get(mimetype, None) or mimetypes.guess_extension(mimetype)

            if ext:
                filename = safe_filename(title) + ext
            else:
                filename = None

        def setter(localname):
            db_entry["localname"] = localname

        url = db_entry.get("href")

        if not url:
            logging.warning("No URL found for %s/%s", cast_uid, episode_uid)
            return None

        # try to fix some common URL errors
        url = url.replace(" ", "%20")

        self.dl.start(
            download_handle,
            setter,
            url,
            dirpath,
            filename,
            fn_prio,
            overwrite,
            expected_size=db_entry.get("length"),
            timeout=self.timeout,
            headers=self.headers,
        )

        """
		try:
			report = ProgressReport()
			(length, localname) = download(db_entry.get("href"), dirpath, filename=filename, fn_prio=fn_prio, overwrite=overwrite, report=report, timeout=self.timeout, headers=self.headers) # download with thread pool

			db_entry["localname"] = localname
			if db_entry.get("length") and db_entry.get("length") != length:
				logging.error("Download succeeded, but size %s doesn't match enclosure length %s", length, db_entry.get("length"))

		except FileExistsError as e:
			db_entry["localname"] = os.path.basename(e.filename)
			if db_entry.get("length") and db_entry.get("length") != os.stat(e.filename).st_size: # can raise not found or sth again
				logging.warning("%s exists, but size %s doesn't match enclosure length %s", e.filename, os.stat(e.filename).st_size, db_entry.get("length"))

		except ContentInvalidLength as e:
			pass
		except HTTPError:
			pass
		except Exception as e:
			logging.exception("Download failed")
		"""

        return db_entry

    def download_items(self, force: bool = False, overwrite: bool = False) -> List[Tuple[str, str]]:

        failed = list()

        for cast_uid, feed in self.db.items():
            for episode_uid in feed["items"]:
                if not self.download_item(cast_uid, episode_uid, force, overwrite):
                    failed.append((cast_uid, episode_uid))

        self.save_local()
        return failed
