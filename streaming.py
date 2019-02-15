from __future__ import absolute_import, division, print_function, unicode_literals

import pickle
from io import open

from feedgen.feed import FeedGenerator
import pafy

from genutility.datetime import now, datetime_from_utc_timestamp

class YoutubeToFeed:

	video_url = "https://www.youtube.com/watch?v={}"

	def __init__(self):

		self.cachefile = "tmp/youtube-cache.p"

		try:
			with open(self.cachefile, "rb") as fr:
				self.cache = pickle.load(fr)
				self.updated = False
		except FileNotFoundError:
			self.cache = dict()
			self.updated = True

	def save_cache(self, force=False):
		if self.updated or force:
			with open(self.cachefile, "wb") as fw:
				pickle.dump(self.cache, fw)

	def get_feed(self, playlist, max_age=None):
		try:
			dt, feed = self.cache[playlist]
			if max_age and now() - dt > max_age:
				raise KeyError
			else:
				return feed
		except KeyError:
			feed = self.create_feed(playlist)
			self.cache[playlist] = (now(), feed)
			self.updated = True
		return feed

	def create_feed(self, playlist, start=None, end=None):
		# playlist must be a youtube playlist url

		pl = pafy.get_playlist(playlist, gdata=False)
		location = 'http://localhost/yt.atom' #request.url

		fg = FeedGenerator()
		fg.load_extension("podcast")
		fg.id(playlist)
		fg.title(pl.get("title"))
		fg.author(name=pl.get("author"))
		fg.subtitle(pl.get("description"))
		fg.link(href=location, rel='self')
		# set updated to latest entry

		for item in pl.get("items")[start:end]:
			m = item.get("playlist_meta")
			p = item.get("pafy")
			size = 0

			stream = p.getbestaudio("ogg")
			mimetype = "audio/webm" # audio/ogg
			if not stream:
				stream = p.getbestaudio("m4a")
				mimetype = "audio/mp4"
			if not stream:
				raise

			created = m.get("time_created")
			if created:
				created = datetime_from_utc_timestamp(created)

			updated = m.get("time_updated")
			if updated:
				updated = datetime_from_utc_timestamp(updated)
			else:
				updated = created

			fe = fg.add_entry()
			fe.id(self.video_url.format(p.videoid))
			fe.title(p.title)
			fe.author( {"name": p.author} )
			fe.content(p.description, type="text")
			#fe.description(p.description, type="text")
			fe.enclosure(stream.url, size, mimetype)
			fe.updated(updated)
			fe.published(created)
			fe.podcast.itunes_duration(p.duration)

		return fg

if __name__ == "__main__":
	gametwo = "https://www.youtube.com/playlist?list=PLztfM9GoCIGrhVwCfF6jsCxCteShsSjXw"
	almost_daily = "https://www.youtube.com/playlist?list=PLsksxTH4pR3I6-7OYZ0GigNnc7KI5S0OK"

	yt = YoutubeToFeed()
	fg = yt.get_feed(playlist=gametwo)
	yt.save_cache()
	with open("gametwo.rss.xml", "wb") as fw:
		fw.write(fg.rss_str(pretty=True))
	with open("gametwo.atom.xml", "wb") as fw:
		fw.write(fg.atom_str(pretty=True))