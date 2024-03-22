""" This is the CLI entrypoint to PodCatcher """

import logging
import time
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser

from genutility.args import is_dir
from genutility.rich import Progress
from rich.logging import RichHandler
from rich.progress import Progress as RichProgress
from rich.table import Table

from .catcher import Catcher
from .utils import DEFAULT_APPDATA_DIR


def make_table_for_status(queued, active, completed, failed) -> Table:
    grid = Table.grid(expand=True)
    grid.add_column()
    for dl in active:
        grid.add_row(f"Downloaded {dl[1]}/{dl[2]} of {dl[0][0]}")
    grid.add_row(f"queued: {len(queued)}, active: {len(active)}, completed: {len(completed)}, failed: {len(failed)}")
    return grid


def wait_for_downloads(c: Catcher, progress: Progress, poll: float = 1.0) -> None:
    while True:
        queued, active, completed, failed = c.get_download_status()
        grid = make_table_for_status(queued, active, completed, failed)
        progress.set_epilog(grid)

        if not queued and not active:
            progress.print("completed")
            for url, localname, length in completed:
                progress.print(f"DONE {localname} {length}")
            progress.print("failed")
            for status, (url, localname, length) in failed:
                progress.print(f"FAILED {url} {status}")

            break

        progress.refresh()
        time.sleep(poll)

    progress.set_epilog()


def main():
    ACTIONS = ["download", "add-feed", "remove-feed", "update-feed", "update-feeds", "update-feed-url"]

    parser = ArgumentParser(description="PodCatcher", formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "action",
        choices=ACTIONS,
    )
    parser.add_argument("--url", help="Feed URL")
    parser.add_argument("--title", help="Feed title")
    parser.add_argument("--appdata-dir", type=is_dir, default=DEFAULT_APPDATA_DIR, help="Path to appdata directory")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    handler = RichHandler()
    FORMAT = "%(message)s"

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[handler])

    c = Catcher(args.appdata_dir)
    feeds_updated = c.load_feeds()

    if args.action == "download":
        if not feeds_updated:
            c.update_feeds()
            feeds_updated = True
        with RichProgress(auto_refresh=False) as p:
            progress = Progress(p)
            c.download_items()
            wait_for_downloads(c, progress)
            c.save_local()

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


if __name__ == "__main__":
    main()
