# Repository Guidelines

## Project Structure & Module Organization

This repository is a small Python camera-monitoring app. Runtime modules live at the repository root: `server.py` hosts the Flask/MJPEG server, `stream_push.py` manages optional ffmpeg relay, and `telegram_sender.py` handles Telegram delivery. The browser UI is `index.html`. Tests are root-level files named `test_*.py`. Research and one-off diagnostics belong in `experiments/`; write generated experiment output to `experiments/output/`, which is ignored by git.

## Build, Test, and Development Commands

Create an isolated environment before installing dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, use the documented startup path:

```bat
py -m pip install -r requirements.txt
start.bat
```

For direct local development, copy `.env.example` to `.env`, fill camera credentials, then run:

```bash
python server.py
python -m unittest discover -p "test_*.py"
```

The server listens on `http://localhost:5050` by default.

## Coding Style & Naming Conventions

Use Python 3.10+ and keep modules script-friendly; current imports support both direct execution and package-style imports. Use 4-space indentation, `snake_case` for functions and variables, and `PascalCase` for classes. Keep configuration in environment variables loaded from `.env`; document new variables in `.env.example` and `README.md`.

Every function and class must have a short Russian documentation comment: use Python docstrings in `.py` files, and JSDoc if JavaScript is added. Add Russian comments before important or non-obvious logic blocks.

## Testing Guidelines

Use the standard `unittest` framework. Add tests near existing root-level test files and name them `test_<behavior>.py`. Prefer mocks for camera, ffmpeg, Telegram, and network behavior so tests can run without hardware or credentials. Run `python -m unittest discover -p "test_*.py"` before submitting changes.

## Commit & Pull Request Guidelines

Recent commits use imperative English summaries, such as `Add ...`, `Update ...`, and `Refactor ...`. Keep commit subjects concise and describe user-visible behavior or the main code change. Pull requests should include a short purpose statement, test results, linked issue if available, and screenshots or logs when UI, Telegram delivery, or streaming behavior changes.

## Security & Configuration Tips

Never commit real `.env` values, camera credentials, Telegram tokens, exported media, or experiment outputs. Keep `.env.example` safe and realistic, with placeholders for sensitive values.
