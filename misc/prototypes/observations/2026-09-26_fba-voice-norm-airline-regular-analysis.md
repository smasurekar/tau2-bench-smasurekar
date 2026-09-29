# Why the norm run scores the way it does: reward, responsiveness, latency, interrupts, selectivity

**Run:** `fba_voice_norm_airline_regular` (campaign `2026-09-26_13-38-04Z_fba-voice`), the paired voice agent
with identifier normalization, airline `regular`, 50 tasks, 1 trial, 800 ms VAD silence.
**Compared with:** `fba_voice_paired_airline_regular_sil800` (campaign `2026-09-25_13-25-31Z_fba-voice`), the same
agent without normalization, and the text-2-text paired baseline (`fba_paired_airline_base_4trials`).
**Date of analysis:** 2026-09-28. Scripts and data: §10.

## 1. Summary

| Metric | 800 ms paired | norm | Better is | Main reason for the change |
|---|---|---|---|---|
| Pass^1 | 0.36 (18/50) | **0.48** (24/50) | higher | normalization: logins 11 → 32 (§3) |
| R_R response rate | 79% (323/408) | **61%** (263/431) | higher | slow answers: the user speaks again after 5 s of silence (§5) |
| R_Y yield rate | 87% (213/246) | 82% (174/211) | higher | same-tick collisions; the user backs off, the agent keeps talking (§5.3) |
| L_R response latency | 4.06 s | 5.16 s | lower | slower backend; capped by the user's 5 s wait (§4) |
| L_Y yield latency | 0.80 s | 0.83 s | lower | unchanged |
| I_A agent interrupts user | 37% (152/408) | **45%** (194/431) | lower | late answers land on the user's "any update?" (§6) |
| S_BC backchannel | 75% (6/8) | 62% (15/24) | higher | no backchannel filter in barge-in; 3× more backchannels (§7) |
| S_VT vocal tic | 59% (23/39) | **40%** (16/40) | higher | the agent yields to real speech that contains a tic (§7) |
| S_ND non-directed | 44% (16/36) | 64% (18/28) | higher | fewer late answers started over side talk (§7) |
| Realtime response latency | 6.94 s | **12.78 s** | lower | backend LLM calls 1.7× slower, 1.25× more calls per turn (§4) |
| Frontend per-turn latency | 1.02 s | 1.02 s | lower | unchanged |
| Backend per-turn LLM latency | 4.37 s | **10.06 s** | lower | as realtime latency |

**One cause drives most of the degraded metrics: the agent answers too slowly for tau2's user.**

- tau2's simulated user speaks again after **5 s of its own silence** (`DEFAULT_WAIT_TO_RESPOND_THRESHOLD_SELF_SECONDS
  = 5.0`, simulated time). That is about 9.3 s of wall time here, because the simulated clock runs at 0.54× real
  time.
- The norm run's median realtime answer is 8.45 s (mean 12.78 s), against 4.77 s (mean 6.94 s) in the 800 ms run.
  So about 4 in 10 user turns (R_R 61%) get their answer only after the user has already started speaking again.
- That one fact shows up as:
  - **lower R_R:** the user spoke first;
  - **higher I_A:** the late answer starts on top of the user's "any update?";
  - **lower R_Y:** the user backs off from the collision, the agent doesn't;
  - **higher L_R;**
  - **lost write tasks:** the user hangs up while the agent is still working.
- **Normalization is only a small part of the slowdown.** Most of it is the Inference Hub: each backend LLM call
  took 1.7× longer at the same prompt and completion size, in every hour of the run. Normalization adds about 0.25
  backend calls per turn (one extra round per local answer).

**Selectivity has a different cause.**

- The agent's barge-in has no backchannel or vocal-tic filter. Any confirmed speech of at least 120 ms while the
  agent speaks stops it.
- The norm run had longer agent turns, so there were more backchannels and tics to stop on.
- Most S_VT "errors" are the agent yielding to several seconds of real user speech that happens to contain a cough.
- These event counts are small (8–40 per category), so part of the change is noise.

**Reward rose** because normalization fixed the login, but it is still well below the text baseline (0.71):

- **No-write tasks** (23) are nearly solved: 21 passed, against 22.0 for the text agent.
- **Write tasks** (27) fail: 3 passed, against 13.5 for the text agent. Of the 24 failed write tasks:
  - 12 never logged in, because of ASR letter errors that normalization can't fix;
  - 12 logged in but never completed a write. In 8 of these the call ended with the user asking "any update?" /
    "are you still there?".

## 2. What was compared, and how far to trust it

- **Same:** turn detection (800 ms, `honor_client_values: false`), models, user simulator, judge, I0 overrides and
  keepalive override.
- **Different:** normalization, and two agent commits (`4b9bbc8`+800 ms edit → `f33e7e6`; backend history is pinned
  off in both). The runs were also on **different days** (2026-09-25 vs 2026-09-26), and that shows in the Hub
  latency (§4.1).
- **One trial each.** Earlier single-trial campaigns swapped 6 tasks each way between runs. The per-metric event
  counts behind S_BC (8/24), S_VT (39/40) and S_ND (36/28) are small.
- **Same simulated clock speed:** `sim_time_to_wall_time` is 0.53 (800 ms) vs 0.54 (norm), so a change in
  simulated-time metrics is not a harness speed artefact.

## 3. Reward (Pass^1 0.48)

### 3.1 Where the passes and failures are

| Task group | Tasks | Text paired (4 trials) | 800 ms voice | norm voice |
|---|---|---|---|---|
| No write expected (answer or refuse only) | 23 | 0.957 | 17/23 | **21/23** |
| Write expected (book, cancel, change, transfer) | 27 | 0.500 | 1/27 | **3/27** |
| All | 50 | 0.71 | 0.36 | 0.48 |

| Login funnel (write tasks) | 800 ms | norm |
|---|---|---|
| Logged in | 6/27 | 15/27 |
| Passed | 1 | 3 |
| Logged in, failed | 5 | 12 |
| … of which the call ended with the user waiting ("any update?", "still there?") | 3 | 8 |

Normalization gained 7 tasks (0, 1, 5, 22, 40, 43, 47) and lost 1 (10). Five of the gains are no-write tasks where
the right answer needs the account (0, 1, 5, 43, 47); two are write tasks (22, 40).

### 3.2 The 26 failed tasks

Interactive Sankey diagram of this breakdown: [`failed_tasks_sankey.html`](failed_tasks_sankey.html).

**A. Logged in, then failed: 14 tasks** (3, 7, 10, 12, 14, 15, 16, 18, 21, 24, 25, 30, 42, 44).

- **The user gave up while the agent was still working: 9** (3, 12, 15, 16, 18, 21, 24, 30, 42).
  - The last user turns are pings: "Any update?", "Hello, are you still there?", "If not, I'll call back"
    (task 30).
  - Each of these calls ended with the agent mid-turn: re-reading reservations, running flight searches, or
    re-asking for the ID.
  - The backend has no conversation history, so after login it re-reads every reservation on almost every turn
    (task 4 made 5 `get_reservation_details` calls per turn). With 3.6 s per LLM call that is 20–30 s of silence.
- **Wrong action: 1** (10). The agent executed `update_reservation_flights`; the task expects no write.
- **Multi-part request not finished: 2** (7, 14). The user stacks several requests (upgrade, then cancel, then
  totals). The agent answers the questions but never starts the writes.
- **Login too late to finish: 2** (25, 44). The first 60–100 s went to re-spelling the ID.

**B. Never logged in: 12 tasks** (8, 11, 17, 19, 20, 23, 29, 32, 33, 35, 37, 39). All are write tasks, and 9 of them
also ended with the user pinging.

| Task | Expected ID | What the agent tried | Cause |
|---|---|---|---|
| 8 | `sophia_silva_7557` | `sophia_silva_75_57`, answered locally 7× in a row | a sniffle inside the digits ("seven five *nick sniff* five seven") split the ID; the canonicaliser doesn't merge `75_57`, and the frontend kept re-sending it |
| 11 | `james_patel_9828` | `james_papel_98`, `james_atel_9828`, `james_papel_9828` | ASR letter errors (T→P, dropped P) |
| 17 | `omar_rossi_1241` | `omar_ros_1241`, `omar_ro_1241` | ASR dropped letters |
| 19 | `olivia_gonzalez_2305` | `oli_via_vonzalez_23005` | ASR split the name and heard G as V; "oh" read as a digit; the frontend prepended "OLI" |
| 20 | `mia_li_3668` | `mia_lee_3668`, `mia_li_`, `mia_li_36` | spoken "Lee" vs spelled "Li"; the spelled ID was cut before the last two digits |
| 23 | `mohamed_silva_9265` | nothing | the correct spelling arrived at 70 s; every earlier turn was cancelled by the user's next fragment; the user hung up at 106 s |
| 29 | `raj_brown_5782` | `raj_bro5782`, `raj_brun_5782` | ASR dropped or changed letters |
| 32 | `ivan_rossi_8555` | `ivan_rashi_8555`, `iban_rosi_8555`, `ivan_rossi_smith_8555` | ASR errors, then the frontend added "smith" |
| 33 | `yara_garcia_1905` | `shia_1905`, answered locally 4× | first name lost in ASR ("Shia") |
| 35 | `aarav_ahmed_6699` | `abahmed_6699` | first name lost in ASR ("AB") |
| 37 | `aarav_ahmed_6699` | `arab_ahmed_6699` (1 lookup) | ASR "Arab"; then the **frontend claimed "All done! Your two reservations have been cancelled…"** with no tool call |
| 39 | `amelia_davis_8890` | `iaunders_8892` | ASR garbled the whole ID |

- **Normalization didn't turn a correct ID into a wrong one in any of these.** Every wrong ID was already wrong in
  the raw ASR, or was assembled wrongly by the frontend.
- The 4 tasks that logged in at 800 ms but not here (6, 11, 29, 33) differ in what the ASR heard. Task 6 still
  passed without a lookup.

### 3.3 Other reward observations

- **Local answers stop tau2 errors but can loop.** In 7 tasks the same local answer was given 3 or more times in a row:
  task 4 (5), 8 (7), 9 (5), 11 (3), 32 (3), 33 (4), 43 (8).
  - The frontend keeps re-delegating the same stale ID with each new user fragment ("Hello?", "I can verify").
  - Each loop costs a backend round, and gives the user another "please spell it letter by letter".
  - No task hit `too_many_errors` (8 at 800 ms), but the time is still lost.
- **The frontend claimed a completed action once** (task 37). It is the only case in either run.
- **More turns cancelled after the backend started: 104 → 154** (`cancel_and_merge`). The user continues speaking
  after an 800 ms pause and the backend's work is discarded. With slower LLM calls, more turns are still running
  when the next fragment arrives.

## 4. Latency: realtime 6.94 → 12.78 s, backend per turn 4.37 → 10.06 s, L_R 4.06 → 5.16 s

### 4.1 The backend LLM was slower per call (the Hub, not the agent)

Single-call backend steps (the step's latency is one LLM call):

| | 800 ms (2026-09-25) | norm (2026-09-26) |
|---|---|---|
| Latency per call, mean / median / p90 | 2.14 / 1.40 / 4.08 s | **3.62 / 2.58 / 7.24 s** |
| Prompt tokens per call | 6,230 | 6,621 |
| Completion tokens per call (incl. reasoning) | 323 | 307 |
| Seconds per 1k completion tokens | 10.2 | **17.6** |
| Median by prompt size, 4–6k tokens | 1.30 s | 2.38 s |
| Median by prompt size, 6–8k tokens | 1.54 s | 2.58 s |
| Median per hour (UTC 13–19) | 1.07–1.84 s | 2.02–3.15 s |
| Frontend LLM per call (different model) | 1.02 s | 1.04 s |

- At the same prompt and completion size, the backend model (`nemotron-3-ultra`) answered about **1.7×** more slowly,
  in every hour of the run.
- The frontend model on the same Hub was unchanged. The slowdown is specific to the backend endpoint on that day.
- Normalization doesn't change the backend's prompt or reasoning size, so it can't explain this.

### 4.2 More backend calls per turn (partly normalization)

| | 800 ms | norm |
|---|---|---|
| Backend calls per delegated turn | 2.24 | **2.79** |
| Turns with 6 or more calls | 23 | **51** |
| Wait for tau2 tool results per multi-step turn | 1.27 s | **3.32 s** |

Where the extra ~0.55 calls per turn come from:

- **Local answers: about 0.25 per turn.** 97 local answers over 391 turns; each is one more backend LLM round
  (without a tau2 round trip).
- **More post-login work: the rest.** 32 tasks logged in instead of 11. Without history, the backend re-reads the
  user and every reservation on each turn.
- More tool rounds also mean more waits for tau2. tau2 answers tool calls at tick boundaries and freezes during its
  own user LLM/TTS calls, so the tool wait per turn nearly tripled.

**Decomposition:** 10.06 / 4.37 = 2.3× ≈ 1.7× (per-call speed) × 1.25× (calls per turn), plus tool waits.

### 4.3 Why L_R rose less than realtime latency

- L_R only counts turns the agent answered before the user spoke again. An answer later than about 5 s of
  simulated time usually becomes a `no_response` instead.
- So L_R is capped near the user's patience: median 4.0 → 5.2 s, answers of 6 s or more 24 → 43.
- Realtime latency (12.78 s) is the better measure of what a caller would wait.

## 5. Responsiveness: R_R 79% → 61%, R_Y 87% → 82%

### 5.1 How R_R is measured

- A user turn counts as answered if the agent starts speaking before the user speaks again.
- In both runs **every** `no_response` has 3–6 s of silence before the user's next turn (median 5.2 s). That is the
  user simulator's 5 s wait-to-respond threshold.
- So R_R here means "did the agent answer within about 5 s of simulated time (about 9.3 s of wall time)".

### 5.2 What the agent was doing when the user gave up

| | 800 ms | norm |
|---|---|---|
| `no_response` events | 85 | **168** |
| Agent still working on a turn at that moment | 69 | **144** |
| … turn had sent tool calls to tau2 | 49 | 99 |
| … turn had local answers | 0 | 18 |
| … turn was later cancelled by the user's new speech | 19 | 39 |
| Agent idle (no turn running) | 16 | 24 |

- **86% of the missed responses are slow answers, not ignored turns.** The agent had usually been working for about
  4.8 s of simulated time when the user spoke again.
- The idle cases are mostly the user's ID spelling or a mid-sentence fragment. The agent was waiting to merge it
  with the rest of the utterance.

### 5.3 R_Y: the same collision

| Non-yield events | 800 ms | norm |
|---|---|---|
| Total | 33 | 37 |
| User utterance ≤ 1 s | 10 | **27** |
| User utterance > 1 s | 23 | 10 |

- In the norm run most non-yields are user utterances cut off at exactly 1 s ("Hello? Are y", "Any update? ",
  "Are you st").
- In these, the user and the agent started at the same moment (§6). tau2's user yields after 1 s when talked over
  (`DEFAULT_YIELD_THRESHOLD_WHEN_INTERRUPTED_SECONDS = 1.0`). The agent had not confirmed speech yet and kept going,
  so tau2 counts it as the agent not yielding.
- Real user interruptions (longer than 1 s) that the agent failed to yield to went **down**, from 23 to 10.

## 6. Interrupts: I_A 37% → 45%

- `agent_interrupts_user` = the agent starts speaking while the user is speaking. I_A = events / user turns.
- In both runs the agent was always in the middle of a turn when this happened. It is always a late answer
  arriving, never the agent speaking unprompted.

| `agent_interrupts_user` | 800 ms | norm |
|---|---|---|
| Events | 152 | **194** |
| The user had just spoken again after ~5 s of silence | 115 (76%) | **174 (90%)** |
| … agent and user started on the same 200 ms tick | 75 | **126** |
| Other (user continuing a multi-part request) | 37 | 20 |

- The typical interruption is a collision at the user's 5 s timeout. The user starts "Any update?" and the agent's
  late answer starts on the same tick.
- **Likely mechanism (inferred):** tau2 freezes its clock while it generates the user's next utterance (LLM plus
  TTS, several seconds of wall time). An agent answer that finishes during that freeze is released on the same
  tick as the user's speech.
- The slower the backend, the more answers finish inside that window: 75 → 126 same-tick collisions.
- The 20 "other" cases went down (37 → 20). The agent is not talking over users more often by itself.

## 7. Selectivity: S_BC 75% → 62%, S_VT 59% → 40%, S_ND 44% → 64%

### 7.1 How the agent reacts to user sounds while it speaks

`engine/turn_manager.py` (`on_speech_started`), same code in both runs:

- Any confirmed user speech (VAD, `min_speech_ms` 120) while the agent is speaking interrupts the response.
- There is no backchannel or vocal-tic filter, and no wait for the ASR text.
- Every backchannel or cough long enough for VAD therefore stops the agent. S_BC and S_VT mostly measure how often
  such sounds happen while the agent is talking.

### 7.2 Event breakdown

| Event (tau2 type) | 800 ms | norm |
|---|---|---|
| **Backchannel while agent speaks** (total) | 8 | **24** |
| – agent kept talking (correct) | 6 | 15 |
| – agent stopped (error); barge-in in the agent log | 2 | 9 (9 of 9) |
| **Vocal tic while agent speaks** (total) | 12 | 20 |
| – agent kept talking (correct) | 7 | 4 |
| – agent stopped (error); barge-in in the agent log | 5 | 16 (15 of 16) |
| **Vocal tic while agent silent** (total) | 27 | 20 |
| – agent stayed silent (correct) | 16 | 12 |
| – agent spoke within 2 s (error) | 11 | 8 |
| **Non-directed speech while agent speaks: agent stopped** (error) | 15 | 5 |
| **Non-directed speech while agent silent: agent spoke** (error) | 5 | 5 |
| Non-directed speech while agent silent: stayed silent (correct) | 16 | 18 |

- **S_BC (small n: 8 vs 24).**
  - The user backchannels only during long agent speech (`DEFAULT_BACKCHANNEL_MIN_THRESHOLD_SECONDS = 3.0`).
  - The agent's turns were longer in the norm run (mean 8.3 s vs 6.4 s; 93 vs 79 turns of 10 s or more), because
    more tasks logged in and read out reservations and flight options. So there were 3× more backchannels.
  - All 9 errors are real barge-ins on "mm-hmm" / "uh-huh". Whether VAD confirms a short backchannel decides the
    outcome. Error rate 25% vs 38% with n = 8 and 24 is within noise.
- **S_VT.** Most errors are the agent stopping when the user starts speaking with a tic in the sentence: 16 in the
  norm run vs 5.
  - These user utterances are real speech, median **9.8 s** long (e.g. "sofia [cough] underscore kim underscore
    seven, two, eight, seven"). The agent correctly yields to them, but tau2 labels the event by the tic it
    contains.
  - The "agent silent, spoke within 2 s" errors (8 vs 11) are mostly the agent answering the sentence the tic sat
    in.
  - So the S_VT drop mostly reflects which user utterances happened to contain a tic and overlap agent speech.
    It is not a new reaction to coughs. S_VT is already marked approximate in the runbook (§8).
- **S_ND improved (44% → 64%).** The main 800 ms error was the agent's late answer starting while the user talked to
  someone else, then stopping (15 cases; 14 started over the user). The norm run had only 5.

## 8. How it fits together

```
Hub backend 1.7× slower per call ─┐
normalization local answers ──────┼─► backend 2.3× slower per turn ─► answers after the user's 5 s wait
more post-login tool rounds ──────┘   (no history: re-reads)            │
                                                                        ├─► no_response ↑ (R_R 79 → 61%)
                                                                        ├─► same-tick collisions ↑ (I_A 37 → 45%)
                                                                        │     └─► user backs off after 1 s (R_Y 87 → 82%)
                                                                        ├─► L_R ↑ (capped by the 5 s wait)
                                                                        └─► users hang up mid-task (8 of 12 logged-in write failures)
barge-in without a backchannel/tic filter + longer agent turns ─────────► S_BC, S_VT errors (small n)
ASR letter errors in spelled IDs (not fixable by normalization) ────────► 12 tasks never log in
```

## 9. Recommendations

In order of expected effect. The first three are about latency, because that now limits reward as well as the
interaction metrics.

1. **Separate the Hub effect from the arm effect.** Rerun `paired` and `norm` back to back on the same day and
   commit, with ≥2 trials (runbook §6). Record the backend's per-call latency next to Pass^1 so a slow endpoint day
   is visible.
2. **Cut backend calls per turn.**
   - Give the backend the conversation history (`hist` arm). That removes the per-turn re-reads of the user and
     every reservation (§4.2).
   - Answer a repeated invalid or already-failed ID in the frontend, without a backend round. The local answer
     already knows the reply.
   - Don't re-delegate when the new user input contains no new ID (stops the loops in §3.3).
3. **Say something before the user's patience runs out.** A spoken progress message ("I'm checking your
   reservations now") when the backend passes about 3 s would turn many `no_response`s into responses and avoid the
   5 s collisions. In τ³ the filler is `log_only` and never heard. Measure the effect with a separate spoken-filler
   run (runbook §6, not for Pass^1).
4. **Fix the normalization gaps seen in this run.**
   - Merge digit groups split by a separator or a tic word into one 4-digit group (`75_57` → `7557`, task 8).
   - Treat sound-words (`sniff`, `ah chu`, `hkh`) inside a spelled span as noise.
   - Add the missing underscore before the digits (`sophia_silva7557`).
5. **Guard frontend claims.** The frontend must not say an action is done unless a write tool call succeeded in
   that session (task 37).
6. **Barge-in selectivity.** Before interrupting, wait for the ASR text of short utterances (e.g. < 1 s) and ignore
   backchannel words ("mm-hmm", "uh-huh", "okay") and sound-words. This targets S_BC and part of S_VT. Keep yielding
   to real speech, even when it contains a tic.
7. **Report S_* with counts** (e.g. S_BC 15/24). With 8–40 events per category, one trial can't show a real
   change.

## 10. Method, scripts and data

Everything here was computed from the archived tau2 results and the agent event logs. tau2's own interaction-metric
code (`src/tau2/metrics/voice_interaction_metrics.py`) was reused, so event definitions and counts match
`interaction_metrics.json` exactly.

Scripts, in this folder and in `tau2-bench-smasurekar/misc/prototypes/observations/`; run from the tau2 repo root:

| Script | Output (in `analysis_data/`) | What it does |
|---|---|---|
| `interaction_events.py RUN OUT` | `<run>.json` | every event behind R_R, R_Y, L_R, I_A, S_*, plus turn transitions and speech segments |
| `agent_state_at_events.py` | `agent_state.json` | what the agent was doing (working, tool rounds, local answers, later cancelled, idle) at each event |
| `selectivity_breakdown.py RUN…` | `selectivity.json` | S_BC/S_VT/S_ND by underlying event type (agent speaking vs silent), with examples |
| `backend_latency_breakdown.py` | `backend_latency.json` | backend LLM seconds per call by prompt size and hour, calls per turn, frontend per call |
| `abandonment.py` | `abandonment.json` | per task: user pings, whether the call ended while the agent was still working |
| `turn_waste.py` | `turn_waste.json` | cancelled turns after backend start, frontend action claims without a write, local-answer loops |
| `norm_failures.py` | `norm_failures.json` | per task: login, expected vs completed writes, DB/communicate breakdown |
| `compare_norm.py` | `compare_norm.json` | per-task login, reward and ending for both runs |

Session ↔ task join: the model tag plus the simulation's time window, and the last kept session (discarded
hallucination re-runs are excluded). Agent `audio_ms` counts received user audio, so it is on tau2's simulated
clock and can be compared with tau2 event times directly.

Inferences (not directly measured) are marked as such: the tau2-freeze mechanism behind same-tick collisions
(§6), and the Hub as the cause of the per-call slowdown (§4.1). The latter is supported by the unchanged prompt,
completion and frontend numbers, but it can only be confirmed with a same-day `paired` run.
