# meet-standin

A Telegram bot running on a spare laptop at home. It joins a weekly Google Meet as your own Google
account and plays an update you recorded as Telegram voice notes. You stay in the call on your
phone, muted, and handle the questions yourself.

**Status:** early. The proof of concept runs first; see [scripts/README.md](scripts/README.md).
The plan and progress are tracked in [#12](https://github.com/dawit-melka/meet-standin/issues/12).

## Development

```sh
uv sync
uv run pytest
uv run ruff check
```

Full setup instructions will come with [#10](https://github.com/dawit-melka/meet-standin/issues/10).
