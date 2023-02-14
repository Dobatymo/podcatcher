from datetime import timedelta
from pathlib import Path
from unittest import TestCase

from podcatcher.catcher import Catcher, parse_itunes_duration


class CatcherTest(TestCase):
    def test_init(self):
        c = Catcher(Path("tests/appdata-test"))
        try:
            c.close()
        except AttributeError:
            pass

    def test_parse_itunes_duration(self):
        result = parse_itunes_duration("12:34:56")
        truth = timedelta(hours=12, minutes=34, seconds=56)
        self.assertEqual(truth, result)

        result = parse_itunes_duration(None)
        truth = None
        self.assertEqual(truth, result)
