# bonobo

An LLM bot that plays (and helps you play) Minecraft. Speedrun under 30 minutes. Plays like a human.

## Run

Requires Python 3.14+, the [Anaka mod](https://github.com/KHN190/anaka/) installed, and a world open.

```sh
./supervise.sh
```

### Configuration

- `MC_INSTANCE` — your Minecraft instance directory, where the API token and logs are read from. Required.
- `MC_API` — the mod's HTTP endpoint. Defaults to `http://127.0.0.1:27599`.
- `MC_DATA` — where runtime data is written. Defaults to `$XDG_DATA_HOME/bonobo`.

## Test

Most of this is testable without the game running:

```sh
python3 -m unittest discover tests     # pure functions, geometry, the planner, recorded replays
python3 mc.py decide diff              # replay recorded decisions against the current code
python3 mc.py scenario run NAME        # one scenario in a disposable test world
```

## License

MIT.
