import logging
import time

from genutility.stdio import print_terminal_progress_line

from catcher import Catcher


def wait_for_downloads(c: Catcher) -> None:
	while True:
		queued, active, completed, failed = c.get_download_status()
		if active:
			print_terminal_progress_line(f"queued: {len(queued)}, active: {len(active)}, completed: {len(completed)}, failed: {len(failed)}. Downloaded {active[0][1]}/{active[0][2]} of {active[0][0][0]}.")
		else:
			print_terminal_progress_line(f"queued: {len(queued)}, active: {len(active)}, completed: {len(completed)}, failed: {len(failed)}")

		if not queued and not active:
			print("completed")
			for localname, length in completed:
				print("DONE", localname, length)
			print("failed")
			for status, localname, length in failed:
				print("FAILED", localname, length, status)

			break

		time.sleep(1)

if __name__ == "__main__":

	from argparse import ArgumentParser

	from genutility.args import is_dir

	from utils import DEFAULT_APPDATA_DIR

	APP_NAME = "podcatcher"
	APP_AUTHOR = "Dobatymo"

	parser = ArgumentParser(description="PodCatcher")
	parser.add_argument("action", choices=["download", "add-feed", "remove-feed", "update-feed", "update-feeds", "update-feed-url", "load", "save"])
	parser.add_argument("--url", help="Feed URL")
	parser.add_argument("--title", help="Feed title")
	parser.add_argument("--appdata-dir", type=is_dir, default=DEFAULT_APPDATA_DIR, help="Path to appdata directory")
	parser.add_argument("-v", "--verbose", action="store_true")
	args = parser.parse_args()

	if args.verbose:
		logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.DEBUG)
	else:
		logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)

	c = Catcher(args.appdata_dir)
	try:
		c.load_local()
		feeds_updated = False
	except FileNotFoundError:
		c.update_feeds()
		c.save_local()
		feeds_updated = True

	if args.action == "download":
		if not feeds_updated:
			c.update_feeds()
			feeds_updated = True
		c.download_items()
		wait_for_downloads(c)

	elif args.action == "add-feed":
		if not args.url:
			parser.error("add-feed requires --url")
		title, feed = c.get_feed(args.url)
		c.add_feed(args.url, args.title or title, feed)

	elif args.action == "remove-feed":
		if not args.title:
			parser.error("remove-feed requires --title")
		c.remove_cast(args.title)

	elif args.action == "update-feed":
		if not args.title:
			parser.error("update-feed requires --title")

		url = c.casts[args.title]["url"]
		logging.info("Updating feed: %s", url)
		_, feed = c.get_feed(url)
		c.update_feed(args.title, feed)
		c.save_local()

	elif args.action == "update-feeds":
		if not feeds_updated:
			c.update_feeds()
			feeds_updated = True

	elif args.action == "update-feed-url":
		if not args.url or not args.title:
			parser.error("update-feed-url requires --url and --title")
		c.update_feed_url(args.title, args.url)

	elif args.action == "load":
		pass

	elif args.action == "save":
		c.save_local()
