import unittest


from stream_push import build_ffmpeg_push_command, should_enable_push


class StreamPushTests(unittest.TestCase):
    def test_build_ffmpeg_push_command_rtmp(self):
        command = build_ffmpeg_push_command(
            source_rtsp_url="rtsp://cam/main",
            target_url="rtmp://example.com/live/cam1",
            transport="tcp",
        )

        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-rtsp_transport", command)
        self.assertIn("tcp", command)
        self.assertIn("-c:v", command)
        self.assertIn("copy", command)
        self.assertEqual(command[-1], "rtmp://example.com/live/cam1")

    def test_build_ffmpeg_push_command_http_flv(self):
        command = build_ffmpeg_push_command(
            source_rtsp_url="rtsp://cam/sub",
            target_url="http://example.com/live/cam1.flv",
            transport="tcp",
        )

        self.assertIn("-f", command)
        self.assertIn("flv", command)
        self.assertEqual(command[-1], "http://example.com/live/cam1.flv")

    def test_should_enable_push(self):
        self.assertFalse(should_enable_push(""))
        self.assertFalse(should_enable_push("   "))
        self.assertTrue(should_enable_push("rtmp://example.com/live/cam1"))


if __name__ == "__main__":
    unittest.main()
