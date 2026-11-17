import tempfile, unittest
from pathlib import Path
from v3core.downloads import DownloadQueue

class QueueTests(unittest.TestCase):
    def test_persistent_queue_and_unique_items(self):
        with tempfile.TemporaryDirectory() as d:
            q=DownloadQueue(Path(d)/"archive.db")
            q.enqueue("1","https://pbs.twimg.com/media/a","image",Path(d)/"a.jpg")
            q.enqueue("1","https://pbs.twimg.com/media/a","image",Path(d)/"a.jpg")
            self.assertEqual(len(q.next_items()),1)
            self.assertEqual(q.summary().get("queued"),1)

if __name__=="__main__": unittest.main()
