#!python3
from future.utils import viewitems
import os, os.path, email.utils, logging, sys, time, mimetypes
from urllib.error import HTTPError

import feedparser
from bson import json_util

from genutility.concurrency import ProgressThreadPool
from genutility.datetime import now
from genutility.filesystem import safe_filename
from genutility.http import URLRequest, ContentInvalidLength
from genutility.iter import first_not_none
from genutility.json import read_json, write_json
from genutility.string import toint

class ProgressReport:

	def __init__(self, file=sys.stdout):
		self.file = file
		self.start = time.time()

	def __call__(self, done, total):
		print("Downloaded {:05.2f}% in {:.0f}s ({:05.2f}mb)".format(done*100/total, time.time()-self.start, total/1024/1024), end="\r", file=self.file)

'''
class DownloadTask:

	def __init__(self):
		# identification
		cast_uid, episode_uid
		# info needed for download
		url, basepath, filename, fn_prio, overwrite, timeout, headers
		# info obtained after download
		status, length, localname
'''

def download(url, basepath, filename=None, fn_prio=None, overwrite=False, suffix=".partial", report=None, timeout=5*60, headers=None):
	return URLRequest(url, headers, timeout).download(basepath, filename, fn_prio, overwrite, suffix, report)

def download_handle(report, setter, url, basepath, filename, fn_prio, overwrite, expected_size=None, timeout=None, headers=None):

	try:
		(length, localname) = download(url, basepath, filename, fn_prio, overwrite, report=report, timeout=timeout, headers=headers)
		status = None

		if expected_size and expected_size != length:
			logging.error("Download succeeded, but size {} doesn't match enclosure length {}".format(length, expected_size))

	except FileExistsError as e:
		localname = os.path.basename(e.filename)
		length = os.stat(e.filename).st_size # can raise not found or sth. again
		status = None

		if expected_size and expected_size != length:
			logging.warning("{} exists, but size {} doesn't match enclosure length {}".format(e.filename, length, expected_size))

	except ContentInvalidLength as e:
		localname = os.path.basename(e.args[0])
		length = e.args[2] # can raise not found or sth. again
		status = e
		logging.warning("%s might be incomplete. Transferred %d/%d", localname, e.args[1], e.args[2])
	except HTTPError as e:
		localname = None
		length = None
		status = e
		logging.exception("Downloading %s failed.", url)
	except Exception as e:
		localname = None
		length = None
		status = e
		logging.exception("Download failed")

	return (setter, status, localname, length) # put some info there to make sure failed downloads can be repeated

class NoTitleError(Exception):
	pass

class InvalidFeed(Exception):
	pass

class Catcher:

	FILENAME_CONFIG = "config.json"
	FILENAME_CASTS = "casts.json"
	FILENAME_FEEDS = "feeds.db.json"

	def __init__(self, appdatadir):
		if not appdatadir:
			raise ValueError("AppDataDir cannot be empty")

		self.appdatadir = appdatadir

		self.load_config()

		self.timeout = self.config["network-timeout"]
		self.user_agent = self.config["user-agent"]
		self.casts_dir = self.config["casts-directory"]
		self.interval = self.config["refresh-interval"] # seconds, unused so far

		self.headers = {"User-Agent": self.user_agent}

		self.dl = ProgressThreadPool()

		self.load_roaming()
		self.load_local()

	def load_config(self):
		self.config = read_json(os.path.join(self.appdatadir, self.FILENAME_CONFIG), object_hook=json_util.object_hook)

	def save_config(self):
		write_json(self.config, os.path.join(self.appdatadir, self.FILENAME_CONFIG), indent="\t", default=json_util.default)

	def load_roaming(self):
		self.casts = read_json(os.path.join(self.appdatadir, self.FILENAME_CASTS), object_hook=json_util.object_hook)

	def save_roaming(self):
		write_json(self.casts, os.path.join(self.appdatadir, self.FILENAME_CASTS), indent="\t", default=json_util.default)

	def load_local(self):
		self.db = read_json(os.path.join(self.appdatadir, self.FILENAME_FEEDS), object_hook=json_util.object_hook)

	def save_local(self):
		write_json(self.db, os.path.join(self.appdatadir, self.FILENAME_FEEDS), indent="\t", default=json_util.default)

	def episode(self, cast_uid, episode_uid):
		assert "|" not in cast_uid and "|" not in episode_uid
		cast = self.db.get(cast_uid)
		if cast:
			return cast["items"].get(episode_uid)
		return None

	def listenedto(self, cast_uid, episode_uid, date=None):
		if not date:
			date = now()
		info = self.episode(cast_uid, episode_uid)
		if not info:
			raise KeyError
		info["listened"] = date
		return date

	def forget_episode(self, cast_uid, episode_uid):
		return self.episode(cast_uid, episode_uid).pop("listened")

	def get_feed(self, url):
		feed = feedparser.parse(url)

		try:
			logging.error("Parsing feed failed: %s", feed.bozo_exception)
		except AttributeError:
			pass

		if len(feed.feed) == 0 and len(feed.entries) == 0: # compare with standard
			raise InvalidFeed("Feed does neither contain a description nor files")

		title = feed.feed.get("title")

		return (title, feed)

	def add_feed(self, url, title, feed):

		assert url is not None
		assert title is not None
		assert feed is not None

		if title in self.casts:
			logging.info("Updating existing cast {}".format(title))

		self.casts[title] = {"url": url}
		self.save_roaming()
		self.update_feed(title, feed)
		try:
			os.makedirs(os.path.join(self.casts_dir, title), exist_ok=True)
			return True
		except FileExistsError:
			return False

	def remove_cast(self, cast_uid, files=False): # fixme: delete files not implemented
		assert (cast_uid in self.casts) == (cast_uid in self.db), "Inconsistent database"

		del self.casts[cast_uid]
		del self.db[cast_uid] # should 'listened to' information be kept?
		self.save_roaming()
		self.save_local()

	def rename_cast(self, cast_uid, name):

		assert (name in self.casts) == (name in self.db), "Inconsistent database"
		assert (cast_uid in self.casts) == (cast_uid in self.db), "Inconsistent database"

		if cast_uid == name: # put after asserts, so inconsistent database is found earlier
			return

		if name in self.casts:
			raise ValueError("Cast already exists")

		if safe_filename(name) != name:
			raise ValueError("Invalid name")

		try:
			os.rename(os.path.join(self.casts_dir, cast_uid), os.path.join(self.casts_dir, name))
		except FileNotFoundError:
			raise ValueError("Cast directory not found")
		except FileExistsError:
			raise ValueError("Directory already exists")

		self.casts[name] = self.casts.pop(cast_uid)
		self.db[name] = self.db.pop(cast_uid)

	def remove_episode(self, cast_uid, episode_uid, file=False):
		return self.episode(cast_uid, episode_uid).pop("localname", None)

	def update_feed(self, cast_uid, feed):
		# changes self.db
		# use length, <itunes:duration>00:51:08</itunes:duration>
		# use length, <itunes:duration>02:10:42</itunes:duration>

		try:
			pub = email.utils.parsedate_to_datetime(feed.feed.published)
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
				entry_pub = email.utils.parsedate_to_datetime(entry.published)
			except AttributeError:
				entry_pub = None

			db_entry.update({"title": entry.get("title"), "date": entry_pub, "duration": entry.get("itunes_duration"), "description": entry.get("description")})

			encs = len(entry.get("enclosures"))
			if encs == 0:
				db_entry.update({"href": None, "length": None, "mimetype": None})
			elif encs == 1:
				enclosure = entry.enclosures[0]
				db_entry.update({"href": enclosure.get("href"), "length": toint(enclosure.get("length")), "mimetype": enclosure.get("type")})
			else:
				raise InvalidFeed("Feed contains multiple enclosures")

	def update_feeds(self):
		logging.debug("Refreshing all feeds")
		for title, cast in viewitems(self.casts):
			feed = feedparser.parse(cast["url"])
			self.update_feed(title, feed)

		self.save_local()

	def get_episode_uid(self, item):
		return first_not_none([item.get("guid"), item.get("link"), item.get("title"), item.get("description")])

	def get_cast_uid(self, cast_title):
		return cast_title

	def get_download_status(self):
		waiting = list((url, basepath, filename, expected_size) for callable, (setter, url, basepath, filename, fn_prio, overwrite), (expected_size, timeout, headers) in self.dl.get_waiting())
		running = list(((url, basepath, filename, expected_size), done, total) for (callable, (setter, url, basepath, filename, fn_prio, overwrite), (expected_size, timeout, headers)), done, total in self.dl.get_running())
		completed = self.dl.get_completed()
		failed = self.dl.get_failed()
		return waiting, running, completed, failed

	def download_item(self, cast_uid, episode_uid, force=False, overwrite=False):
		# changes self.db

		fn_prio = self.casts[cast_uid].get("filename", None)
		if fn_prio:
			fn_prio = (fn_prio,)
		else:
			fn_prio = None

		assert safe_filename(cast_uid) == cast_uid
		dirpath = os.path.join(self.casts_dir, safe_filename(cast_uid)) # make sure still unique

		db_entry = self.episode(cast_uid, episode_uid)

		if not force and db_entry.get("localname", None):
			return

		if not db_entry.get("title") or not db_entry.get("mimetype"):
			filename = None
		else:
			ext = mimetypes.guess_extension(db_entry.get("mimetype"))
			if ext:
				filename = safe_filename(db_entry.get("title")) + ext
			else:
				filename = None

		def setter(localname):
			db_entry["localname"] = localname

		self.dl.start(download_handle, setter, db_entry.get("href"), dirpath, filename, fn_prio, overwrite, expected_size=db_entry.get("length"), timeout=self.timeout, headers=self.headers)

		"""
		try:
			report = ProgressReport()
			(length, localname) = download(db_entry.get("href"), dirpath, filename=filename, fn_prio=fn_prio, overwrite=overwrite, report=report, timeout=self.timeout, headers=self.headers) # download with thread pool

			db_entry["localname"] = localname
			if db_entry.get("length") and db_entry.get("length") != length:
				logging.error("Download succeeded, but size {} doesn't match enclosure length {}".format(length, db_entry.get("length")))

		except FileExistsError as e:
			db_entry["localname"] = os.path.basename(e.filename)
			if db_entry.get("length") and db_entry.get("length") != os.stat(e.filename).st_size: # can raise not found or sth again
				logging.warning("{} exists, but size {} doesn't match enclosure length {}".format(e.filename, os.stat(e.filename).st_size, db_entry.get("length")))

		except ContentInvalidLength as e:
			pass
		except HTTPError:
			pass
		except Exception as e:
			logging.exception("Download failed")
		"""

		return db_entry

	def download_items(self, force=False, overwrite=False):

		failed = list()

		for cast_uid, feed in viewitems(self.db):
			for episode_uid in feed["items"]:
				if not self.download_item(cast_uid, episode_uid, force, overwrite):
					failed.append((cast_uid, episode_uid))

		self.save_local()
		return failed
