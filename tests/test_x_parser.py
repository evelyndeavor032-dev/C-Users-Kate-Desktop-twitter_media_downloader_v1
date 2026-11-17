import unittest
from v3core.x_parser import extract_posts

class ParserTests(unittest.TestCase):
    def test_legacy_photo_and_video(self):
        payload={"data":{"tweet":{"rest_id":"123","legacy":{"full_text":"hello","created_at":"Mon Jan 01 00:00:00 +0000 2024","extended_entities":{"media":[{"type":"photo","media_url_https":"https://pbs.twimg.com/media/a.jpg"},{"type":"video","media_url_https":"https://pbs.twimg.com/media/b.jpg","video_info":{"variants":[{"content_type":"video/mp4","bitrate":10,"url":"https://video.twimg.com/a.mp4"}]}}]}}}}}
        posts=extract_posts(payload)
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0]["id"],"123")
        self.assertEqual([m["type"] for m in posts[0]["media"]],["image","video"])
    def test_deduplicates_nested_tweet(self):
        tweet={"rest_id":"9","legacy":{"full_text":"text"}}
        self.assertEqual(len(extract_posts({"a":tweet,"b":tweet})),1)

if __name__=="__main__": unittest.main()
