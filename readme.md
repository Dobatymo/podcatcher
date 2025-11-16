# Podcatcher

Simple podcast / RSS download app.

## Requirements
- Python 3.8 (or newer)

## Install (option 1: with uv)

- Run GUI: `uv run podcatcher-web` and open `localhost:8000` in your browser to connect to the GUI.
- Run CLI: `uv run podcatcher-cli`.

## Install (option 2: without uv)

- Install: `pip install .`
- Run GUI: `podcatcher-web` (or `python -m podcatcher.web`) and open `localhost:8000` in your browser to connect to the GUI.
- Run CLI: `podcatcher-cli` (or `python -m podcatcher.cli`).

## Development

Run tests: `uv run -m unittest discover -v -s tests`
