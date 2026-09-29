# Jar HTTP API

Anaka mod at `MC_API` (default `http://127.0.0.1:27599`), `Authorization: Bearer <token>` from
`$MC_INSTANCE/config/agent-bridge.json`. Client: `bonobo/api.py` (`get`, `post`, `run`, `run_chain`).

| Endpoint | Use |
|---|---|
| `GET /status` | mod version, features |
| `GET /state` | body: pos, look, health, food, light, dimension, control, lastDamage |
| `GET /inventory` | bag and equipment |
| `GET /entities?radius=` | nearby entities; combat fields (velocity, tti_ticks, impact, in_reach…) |
| `GET /container` | the open screen's slots |
| `GET /blocks?…` | a region's blocks |
| `GET /find?…` | block search (`exposed=true`: open faces only) |
| `GET /dark?…` | spots at low block light |
| `GET /plan?to=…` | path plan and seconds, no walking |
| `POST /task?wait=0` | queue tasks (`{"tasks": [...]}`); `GET /task?id=` watches one |
| `GET /tasks` | recent tasks |
| `POST /stop` | cancel the running task |
| `POST /takeover`, `/release` | the agent drives / hands back |
| `POST /control` | pause as the player's toggle key does |
| `POST /resume` | close the pause menu |
| `POST /respawn` | respawn after death |
| `POST /reflex` | reflex policy (shield, counter, deflect); `GET` adds last and recent acts |
| `POST /autoeat` | eat-while-walking policy |
| `POST /click`, `/close` | inventory clicks; close the screen |
| `POST /button`, `/trade`, `/rename` | screens with buttons |
| `POST /chat` | a chat line or command |
| `GET /events?since=` | jar event ring (damage, …) |
| `GET /combat/frames?since=` | recorded combat frames |

`POST /hud` is tried by `intent.py`; jars without it fall back to chat.
