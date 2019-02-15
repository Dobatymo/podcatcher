#! python3

import logging
from argparse import ArgumentParser

from catcher import Catcher

if __name__ == "__main__":

	logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.DEBUG)

	parser = ArgumentParser(description='PodCatcher')
	parser.add_argument("action", choices=["download", "updatefeed", "removefeed"])
	parser.add_argument("--url")
	parser.add_argument("--title")

	args = parser.parse_args()

	c = Catcher("config-home.json")
	try:
		c.load_local()
	except FileNotFoundError:
		c.update_feeds()
		c.save_local()

	if args.action == "download":
		c.update_feeds()
		c.update_files()

	elif args.action == "updatefeed":
		c.add_feed(args.url, args.title)

	elif args.action == "removefeed":
		c.remove_feed(args.title)
