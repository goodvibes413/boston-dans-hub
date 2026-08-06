# Monitoring — how we know Dan is actually up

Three layers, each covering what the one before it structurally cannot.
The important idea: **a monitor that shares fate with the thing it watches
cannot report that thing being dead.** Each layer exists to break one more
shared-fate link.

| Layer | Runs where | Catches | Blind to |
|---|---|---|---|
| 1. In-job alerting (`morning_brew.yml`) | inside the pipeline job | steps that fail while the job runs | anything that stops the job from running; stale/fallback publishes (they exit 0) |
| 2. Content watchdog (`content_watchdog.yml`) | separate Actions job | stale/fallback content, missed days, failed Pages deploy — regardless of what the pipeline reported | runner starvation (it needs a runner too) |
| 3. External uptime check | outside GitHub entirely | everything above **plus** GitHub not scheduling anything at all | nothing in this system — but it is third-party and needs its own account |

Layers 1 and 2 are in this repo. Layer 3 is deliberately **not**, because
putting it here would defeat its purpose.

---

## Why layer 3 is necessary

On 2026-07-24 and 2026-08-06 the Morning Brew job was never assigned a
runner: `runner_id: 0`, empty `runner_name`, no `steps` array at all, and
cancelled after ~15 minutes queued. Because no step executed, the
`if: failure() || cancelled()` issue-filing step could not run either. The
run showed red in the UI and produced no alert.

On 2026-08-06 the content watchdog's own first run was stranded the same
way, alongside an `ensure-labels` dispatch. **Three separate workflows,
none of which got a runner.**

A note on reasoning that turned out to be wrong, recorded so it is not
repeated: the watchdog was originally justified partly on being "a checkout
plus one stdlib script, far likelier to get scheduled than the 25-minute
pipeline." That is false. Queue admission is about obtaining a runner at
all; it is indifferent to how long the job would then hold one. GitHub
cannot know a job is cheap before running it. A 10-second job waits in the
same line as a 25-minute one.

So: **no workflow in this repository can alert on this repository failing to
get runners.** That is the gap layer 3 closes, and the only way to close it
is from outside GitHub Actions.

---

## Layer 3: the external check

### What to poll

```
https://goodvibes413.github.io/boston-dans-hub/data/daily_output.json
```

This is the file the site actually fetches, so polling it verifies the whole
chain end to end — pipeline ran, content published, Pages deployed, CDN
serving. Nothing else needs to be reachable.

### What "healthy" means

The response is JSON. Dan is healthy when **all** hold:

| Condition | Meaning if violated |
|---|---|
| `generated_at` is less than ~28 hours old | no publish happened today |
| `_stale` is absent or false | serving a previous day's content as a fallback |
| `_fallback` is absent or false | serving the "Dan's takin' the mornin' off" placeholder |

The 28-hour threshold matters. Publish slots run 08:00–15:00 UTC nominal,
but observed queue delays reach ~115 minutes, so a good day can legitimately
publish as late as ~17:00 UTC. A 24-hour window would false-alarm on the
normal spread between an early publish one day and a late one the next.
28 hours absorbs that and still fires well within a day of a genuine miss.

### Option A — UptimeRobot (free tier, no code)

Free plan allows keyword monitors on a 5-minute interval, which is far more
often than needed; 30 or 60 minutes is plenty.

1. New monitor → type **Keyword**
2. URL: the `daily_output.json` URL above
3. Keyword: `"_fallback"` → alert **when keyword exists**
4. Add a second monitor, same URL, keyword `"_stale"` → alert when it exists

This catches degraded publishes but **not** a missed day, because a missing
key is not a keyword match and UptimeRobot cannot compare timestamps. Pair
it with Option B, or treat it as partial coverage.

### Option B — any scheduled runner you control (complete coverage)

Anywhere that is not GitHub Actions: a cron job on a always-on box, a free
Cloudflare Worker on a Cron Trigger, a Val Town scheduled val, a Deno Deploy
cron. The check is small enough to inline:

```python
#!/usr/bin/env python3
"""Alert if Boston Dan is not serving current content. Run once or twice
daily from anywhere that is NOT GitHub Actions."""
import json
import sys
import urllib.request
from datetime import datetime, timezone

URL = "https://goodvibes413.github.io/boston-dans-hub/data/daily_output.json"
MAX_AGE_HOURS = 28  # see rationale above

req = urllib.request.Request(URL, headers={"Cache-Control": "no-cache"})
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode())

problems = []
raw = data.get("generated_at")
if not raw:
    problems.append("no generated_at field")
else:
    gen = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
    if age > MAX_AGE_HOURS:
        problems.append(f"content is {age:.1f}h old (limit {MAX_AGE_HOURS}h)")

if data.get("_stale"):
    problems.append(f"stale republish: {data.get('_stale_reason', 'unknown')}")
if data.get("_fallback"):
    problems.append("serving SAFE_FALLBACK")

if problems:
    print("DAN IS DOWN: " + "; ".join(problems))
    sys.exit(1)
print(f"ok — published {raw}")
```

Wire the non-zero exit to whatever notifies you (email, Pushover, a Slack
webhook). Stdlib only, no dependencies, same as the rest of this project.

### Recovery

Whatever fires, the fix is the same — re-run the pipeline once Gemini and
GitHub capacity are both healthy:

```
gh workflow run "Morning Brew — Daily Dan Commentary" --ref main -f force=true
```

---

## Deliberately not done

**Reducing the cron slots.** The 15:00 UTC slot is the one that keeps
getting stranded, and by that hour the day's content has published every
time in the observed sample — so it is tempting to drop it. Left alone for
now because it is genuinely the last safety net on a day when Gemini is
down all morning, and the cost of keeping it is cosmetic red X's rather than
anything readers see. Revisit if the noise becomes annoying enough to
outweigh that.

**Alerting on the red X itself.** GitHub notifies on failed runs already.
The problem was never missing notifications for runs that fail — it was runs
that fail *without running*, and content that degrades while every run stays
green. Layers 2 and 3 target those.
