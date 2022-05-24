import email.utils
import unittest


class TestCatcher(unittest.TestCase):
    def test_parse_pubdata(self):
        dates = ("Tue, 21 Mar 2017 00:00:00 GMT", "Wed, 22 Mar 2017 15:11:38 +0000", "Wed, 15 Mar 2017 13:06:00 -0400")

        for date in dates:
            email.utils.parsedate_to_datetime(date)


if __name__ == "__main__":
    unittest.main()
