# bonobo

A bot that plays Minecraft survival through the Anaka mod: speedrun-minded, safe at night.

## Run

Python 3.14+, the [Anaka mod](https://github.com/KHN190/anaka/), a world open.

```sh
./supervise.sh            # autoplay, detached; wakes you on death, stuck, errors
python3 mc.py state       # one-off commands: state inv plan task find stop release …
```

Environment:

- `MC_INSTANCE` — Minecraft instance dir (API token in `config/agent-bridge.json`). Required.
- `MC_API` — the mod's HTTP endpoint. Default `http://127.0.0.1:27599`.
- `MC_DATA` — runtime data. Default `$XDG_DATA_HOME/bonobo` (else `~/.local/share/bonobo`).

## Test

```sh
./runtests.py --fast                      # offline suite, one process per file (skips replays)
python3 -m unittest tests.test_layers     # one module
npx -y pyright@1.1.414                    # must stay 0
python3 mc.py scenario run NAME           # one bench row, in a test world only
```

Docs: `docs/architect.md` (layers, modules, data), `docs/api.md` (jar HTTP).

## License

MIT.
