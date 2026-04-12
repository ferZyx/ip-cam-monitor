import unittest
from unittest.mock import MagicMock, patch


from stream_push import RemotePushRelay, build_ffmpeg_push_command, should_enable_push


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

    def test_build_ffmpeg_push_command_transcode_h264(self):
        command = build_ffmpeg_push_command(
            source_rtsp_url="rtsp://cam/main",
            target_url="rtmp://example.com/live/cam1",
            transport="tcp",
            video_codec="libx264",
            preset="veryfast",
            tune="zerolatency",
            fps=12,
            scale_height=720,
        )

        self.assertIn("-c:v", command)
        self.assertIn("libx264", command)
        self.assertIn("-preset", command)
        self.assertIn("veryfast", command)
        self.assertIn("-tune", command)
        self.assertIn("zerolatency", command)
        self.assertIn("-r", command)
        self.assertIn("12", command)
        self.assertIn("-vf", command)
        self.assertIn("scale=-2:720", command)

    def test_should_enable_push(self):
        self.assertFalse(should_enable_push(""))
        self.assertFalse(should_enable_push("   "))
        self.assertTrue(should_enable_push("rtmp://example.com/live/cam1"))

    @patch("stream_push.subprocess.Popen")
    def test_remote_relay_uses_pipe_logging_when_enabled(self, popen_mock):
        process = MagicMock()
        process.poll.return_value = None
        process.stdout = None
        popen_mock.return_value = process

        relay = RemotePushRelay(log_to_console=True)
        relay.ensure_running("rtsp://cam/main", "rtmp://example.com/live/cam1")

        _, kwargs = popen_mock.call_args
        self.assertEqual(kwargs["stdout"], -1)
        self.assertEqual(kwargs["stderr"], -2)
        self.assertTrue(kwargs["text"])

    @patch("stream_push.subprocess.Popen")
    def test_remote_relay_silences_logs_when_disabled(self, popen_mock):
        process = MagicMock()
        process.poll.return_value = None
        popen_mock.return_value = process

        relay = RemotePushRelay(log_to_console=False)
        relay.ensure_running("rtsp://cam/main", "rtmp://example.com/live/cam1")

        _, kwargs = popen_mock.call_args
        self.assertEqual(kwargs["stdout"], -3)
        self.assertEqual(kwargs["stderr"], -3)


if __name__ == "__main__":
    unittest.main()
