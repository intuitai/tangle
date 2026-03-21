# tests/test_cli.py

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestCLIMain:
    @patch("tangle.cli.uvicorn")
    @patch("tangle.cli.argparse.ArgumentParser.parse_args")
    def test_main_default_args(self, mock_parse_args, mock_uvicorn) -> None:
        """main() with default arguments creates app and runs uvicorn."""
        mock_parse_args.return_value = MagicMock(host="0.0.0.0", port=8090)

        from tangle.cli import main

        main()

        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs[1]["host"] == "0.0.0.0"
        assert call_kwargs[1]["port"] == 8090

    @patch("tangle.cli.uvicorn")
    @patch("tangle.cli.argparse.ArgumentParser.parse_args")
    def test_main_custom_host_port(self, mock_parse_args, mock_uvicorn) -> None:
        """main() propagates custom --host and --port to uvicorn."""
        mock_parse_args.return_value = MagicMock(host="127.0.0.1", port=9999)

        from tangle.cli import main

        main()

        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs[1]["host"] == "127.0.0.1"
        assert call_kwargs[1]["port"] == 9999

    @patch("tangle.cli.uvicorn")
    @patch("tangle.cli.argparse.ArgumentParser.parse_args")
    def test_main_calls_stop_on_cleanup(self, mock_parse_args, mock_uvicorn) -> None:
        """The finally block calls monitor.stop() even when uvicorn.run exits."""
        mock_parse_args.return_value = MagicMock(host="0.0.0.0", port=8090)

        with patch("tangle.cli.TangleMonitor") as MockMonitor:
            monitor_instance = MockMonitor.return_value
            from tangle.cli import main

            main()
            monitor_instance.stop.assert_called_once()

    @patch("tangle.cli.uvicorn")
    @patch("tangle.cli.argparse.ArgumentParser.parse_args")
    def test_main_stop_called_on_exception(self, mock_parse_args, mock_uvicorn) -> None:
        """monitor.stop() is called even if uvicorn.run raises."""
        mock_parse_args.return_value = MagicMock(host="0.0.0.0", port=8090)
        mock_uvicorn.run.side_effect = KeyboardInterrupt()

        with patch("tangle.cli.TangleMonitor") as MockMonitor:
            monitor_instance = MockMonitor.return_value
            from tangle.cli import main

            with pytest.raises(KeyboardInterrupt):
                main()
            monitor_instance.stop.assert_called_once()
