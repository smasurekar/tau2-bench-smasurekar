# Observations: τ³ voice, Frontend/Backend Agent, paired arm, airline, `regular`

**Campaign:** `2026-09-24_08-47-49Z_fba-voice` · **Run:** `fba_voice_paired_airline_regular` ·
**Written:** 2026-09-24 ~11:50 UTC (§4.5 examples added ~12:00 UTC), while the run was in progress (**31 of 50 tasks finished**). **Final 50-task results: §9** (run ended 13:47:28Z, Pass^1 0.36).
Numbers below cover tasks 0–30 only. The final numbers come from the §7 report after the run ends.

Runbook: [`../voice-frontend-backend-agent-tau3-runbook.md`](../voice-frontend-backend-agent-tau3-runbook.md) ·
Integration plan: [`../voice-frontend-backend-agent-tau3-integration-plan.md`](../voice-frontend-backend-agent-tau3-integration-plan.md)

Paths use the runbook's variables:

```
TAU2  = /localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar
AGENT = /localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar
DUMP  = /localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump
```

---

## 1. Summary

- **Voice Pass^1 is 0.35 on the first 31 tasks.** On the same 31 tasks, the text-2-text runs of the
  same agent score 0.73 (paired) and 0.81 (backend-only). Over all 50 tasks, text paired is 0.71 and
  text backend-only is 0.79.
- **The harness is clean.** No disconnects, no infrastructure errors, no failed user-simulator, judge
  or TTS calls. The only harness bug found (a WebSocket keepalive disconnect) was fixed before this
  run (§3).
- **Almost the whole gap is authentication.** The agent cannot turn the user's spoken, letter-by-letter
  user ID into the exact lower-case string the airline DB needs:
  - ASR heard the full user ID correctly at least once in **15/31** tasks.
  - The frontend passed a correct ID (ignoring case) to the backend in **8/31**.
  - `get_user_details` succeeded in only **6/31** (in text it is close to 31/31).
- **The 0.35 overstates the agent.** 10 of the 11 passes are tasks whose correct outcome needs no
  booking change, so an agent that never authenticates still passes them. Of the **19 tasks that need
  a write action** (booking, change, cancellation or transfer), **only task 30 passed**.
- Five causes compound (§4): ASR splits spelled IDs into fragments, ASR gets letters wrong, IDs come out
  upper-case, the frontend corrupts IDs and the backend never sees the raw transcript, and wasted
  lookups trigger tau2's 10-tool-error cap.
- The OpenAI Realtime protocol itself is not the problem. The voice conditions tau2 sends through it
  are (§5): 8 kHz μ-law telephone audio, 500 ms end-of-turn silence, and speech-to-speech instructions
  that demand letter-by-letter spelling.

## 2. Run setup and status

| Item | Value |
|---|---|
| Arm | `paired` only (frontend + backend). The backend-only arm was **not run, at the user's request** |
| Agent container | `fba-voice`, port 8765, profile `profiles/tau3_eval.yaml` |
| Frontend LLM | `nvidia/nvidia/nemotron-3.5-lightning` (reasoning off) |
| Backend LLM | `nvidia/nvidia/nemotron-3-ultra` (reasoning on, budget 1024) |
| Agent ASR / TTS | NeMo Speech (`nemotron-voice-agent-nemo-speech-1`) |
| User simulator LLM, judge, hallucination check | `azure/openai/gpt-5.2` on the Inference Hub |
| User TTS | `openai/openai/gpt-4o-mini-tts` on the Hub (I0 override; not ElevenLabs) |
| User backchannel/interruption decisions | `azure/openai/gpt-4.1` on the Hub (I0 override) |
| Domain / split / complexity | airline / `base` (50 tasks) / `regular` |
| Trials / concurrency | 1 / 1 |
| Agent model tag | `pine-fba-voice-paired-airline-regular` |
| Agent commit | `4b9bbc8` (I1 per-role usage live) |
| Started | 2026-09-24T08:47:49Z, in tmux session `tau3_airline` |
| Status at writing | 31/50 done, avg reward 0.35, 0 disconnects, 0 infra retries, 0 failed LLM calls. Mean ≈ 5.5 min/task (sim time); expected end ≈ 13:25 UTC |

Hallucination re-runs happened on a few tasks (e.g. task 2), where tau2 flagged the simulated user and
re-ran the task with feedback. This is expected tau2 behaviour, not a fault.

## 3. Harness changes and events before this run

1. **Earlier partial run aborted and set aside.** A first `fba_voice_paired_airline_regular` attempt
   reached 6/50 and was stopped at 08:33. Its data was renamed, not deleted:
   `$TAU2/data/simulations/_aborted_fba_voice_paired_airline_regular_0833/` and
   `$TAU2/data/simulations/_consoles/_aborted_fba_voice_paired_airline_regular_0833.{log,start,end}`.
   An earlier aborted backend-only attempt is at
   `$TAU2/data/simulations/_aborted_fba_voice_bo_airline_regular_0814/`.
2. **Client-side WebSocket keepalive disconnects fixed.** The aborted run kept hitting
   `RuntimeError: Not connected to API`, even after the agent server's keepalive was turned off
   (`server.ws_ping_interval_s: 0`). The cause was tau2's own client:
   `websockets.connect(...)` at `$TAU2/src/tau2/voice/audio_native/openai/provider.py:201` uses the
   default 20 s ping. tau2 blocks its event loop during slow user TTS/LLM calls, the ping goes
   unanswered, and the client closes the socket itself (agent log: `session closed`; tau2: `Not
   connected` 2 s later).
   - Fix, in I0 only (`src/tau2` untouched):
     `$TAU2/misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py` (`_NoPingWebsockets`, line 170)
     sets `ping_interval=None` for tau2's Realtime provider.
   - `$TAU2/misc/prototypes/fba_voice_eval/tau2_ihub.py` logs `REALTIME CLIENT KEEPALIVE OVERRIDE`
     at start.
   - Result: 0 disconnects so far in this run.
3. **Campaign folder name now carries the full UTC start time.** The runbook stores it in
   `$TAU2/data/simulations/_consoles/CAMPAIGN` (`2026-09-24_08-47-49Z_fba-voice`).
4. **One probe session** (`pine-probe-keepalive`, on `fba-voice-bo`) was used to verify the keepalive
   fix. It has its own model tag and is not part of any run.

## 4. Why the reward drops in voice

### 4.1 Funnel (tasks 0–30)

| Stage | Tasks | Text equivalent |
|---|---|---|
| ASR heard the complete user ID correctly at least once (fragments joined) | 15 / 31 | user types it exactly |
| Frontend passed the correct ID (case-insensitive) to the backend | 8 / 31 | ~31 / 31 |
| Frontend passed it exactly (correct case) | 1 / 31 | ~31 / 31 |
| `get_user_details` succeeded with the correct ID | **6 / 31** | ~31 / 31 |

`get_user_details` calls: 156 in total. 16 succeeded, **20 failed only because of letter case**, and
120 failed on wrong characters.

### 4.2 Causes

#### How much each cause contributes (tasks 0–30)

Two views. Method in §8.

**By failed tool call.** All 151 tool calls that returned `Error: … not found` in the 31 tasks' final
agent sessions. Each call gets exactly one label, checked in the order below.

| # | Label (order checked) | Cause in this section | Failed calls | Share |
|---|---|---|---|---|
| 1 | Right characters, wrong case (lower-cased argument equals the correct ID) | 3. Upper case | 19 | 13% |
| 2 | Exact repeat of an ID that had already failed in the same task | 4. No backend memory, 5. re-ask loop | 43 | 28% |
| 3 | Incomplete ID: a user ID without all three parts / 4 digits, or a reservation code that is not 6 characters | 1. Turn fragments | 41 | 27% |
| 4 | Wrong ID although the ASR transcript already held the correct one at call time | 4. Frontend corruption | 9 | 6% |
| 5 | Wrong ID, and the ASR had not yet produced the correct one | 2. ASR letter errors | 38 | 25% |
| 6 | Other | — | 1 | 1% |
| | **Total** | | **151** | 100% |

Almost half of the failed calls (labels 2 and 3, 55%) are not new recognition errors. They are lookups
the agent should not have made: partial IDs from turn fragments, and retries of IDs already known to
be wrong. Both come from the turn-fragmentation / stateless-backend design, and together they use up
the 10-error cap.

**By failed task.** 20 of the 31 tasks failed. Each failed task gets one *primary* cause: the first
blocker that, if removed, would have let the task authenticate.

| Primary cause | Failed tasks | Share | Tasks |
|---|---|---|---|
| Blocked only by upper case (the agent had the right characters) | 4 | 20% | 9, 18, 19, 24 |
| ASR heard the ID, but it was lost in turn fragments / the frontend | 3 | 15% | 3, 8, 13 |
| ASR never produced the correct ID | 9 | 45% | 14, 15, 16, 17, 21, 22, 23, 25, 29 |
| Authenticated, then agent errors (stateless loops, lost authentication, no write) | 3 | 15% | 7, 11, 12 |
| Never asked for or looked up the user ID | 1 | 5% | 20 |
| **Total** | **20** | 100% | |

In total, 16 of the 20 failed tasks (80%) never authenticated. The first three rows are all voice
problems.

**Contributing causes.** A failed task usually has several causes at once. This counts failed tasks
with at least one error of each kind (of 20):

| Contributing cause | Failed tasks | Share |
|---|---|---|
| ASR letter errors | 15 | 75% |
| Retries of an ID that already failed | 13 | 65% |
| Incomplete IDs from turn fragments | 12 | 60% |
| Upper case | 6 | 30% |
| Ended by the 10-tool-error cap (`too_many_errors`) | 6 | 30% |
| Frontend garbled an ID the ASR had heard correctly | 5 | 25% |

The 75% for ASR errors can overcount when ASR spells a correct ID in a form the normalisation (§8)
does not recognise. The case, retry and fragment counts are exact.

#### The causes

**1. Spelled IDs are split into many turns.** (41 failed calls, 27%; in 12 of 20 failed tasks)
- The simulated user must spell IDs with commas, e.g. "A, A, R, A, V, underscore, …"
  (`$TAU2/data/tau2/user_simulator/simulation_guidelines_voice.md:17`,
  `simulation_guidelines_voice_tools.md:22`). The TTS renders each comma as a pause.
- The agent ends the user's turn after 500 ms of silence (§5.2), so one ID arrives as 3–6 ASR
  segments. A task averages ≈27 ASR segments.
- Each segment is a full agent turn: frontend decision → backend → tool call. Partial IDs get looked
  up, e.g. task 9: `AARAV_A`, `AARAV_66`, `AARAB_6699`; task 0: "E M M Underscore K I M underscore
  Nine Nine".

**2. ASR letter errors.** (38 failed calls, 25%; primary cause of 9 failed tasks, 45%; in 15 of 20) The audio is narrowband telephone audio (§5.1), so letters that sound alike
get confused:

| Said | ASR heard | Task |
|---|---|---|
| V | B (`AARAB`) | 9 |
| "I, V, A, N" | "I D A N" (`IDA_MULLER_7015`) | 25 |
| "emma underscore kim" | "Emma underscore Tim" | 0 |
| reservation `H, 9, Z, U, 1, C` | "H nine V U one C" | 24 |
| reservation `X, E, W, R, D, 9` | "E W R D nine" (X dropped) | 13 |
| reservation `E, H, G, L, P, 3` | "G.L." | 0 |
| "J, A, M, E, S" | "J .A.M. / E F" | 13 |

**3. IDs come out upper-case.** (19 failed calls, 13%; primary cause of 4 failed tasks, 20%)
- Spelled letters are transcribed as capitals ("A A R A V"), and the agent never lower-cases them.
- Airline user IDs are lower-case and `get_user_details` is case-sensitive.
- In **5 tasks (4, 9, 18, 19, 24)** the agent had the right characters and failed only on case. Task 4
  passed anyway, because its correct outcome needs no change. Examples: `AARAV_AHMED_6699`, `MIA_KIM_4397`, `Olivia_Gonzalez_2305`.

**4. The frontend corrupts IDs, and the backend only sees the frontend's summary.** (9 failed calls
(6%) where the frontend garbled an ID the ASR had right; plus most of the 43 retries (28%); primary
cause of 3 failed tasks after authentication, 15%)
- In paired mode each backend turn starts from an empty history and gets only the frontend's
  `call_backend` query text: `History()` at
  `$AGENT/src/prototypes/text_frontend_backend_agent/agent.py:117`, and `Backend._apply_input` in
  `backend.py`. The raw ASR transcript never reaches the backend.
- Task 9 shows the corruption: ASR heard "A A R A V underscore / A H M E D underscore six six nine
  nine" correctly, and the frontend (`nemotron-3.5-lightning`, no reasoning) wrote `AAVAV_AHMED_6699`.
- The stateless backend also repeats work and forgets progress:
  - task 11 fetched reservation `GV1N64` 12 times and repeated the same flight searches;
  - task 7 authenticated as `daiki_muller_1116`, then went back to `DAIKI_MULLER_1116` on later turns
    and lost the authentication.
- In text the same design works, because one tau2 user message is one complete turn with the ID typed
  exactly.

**5. The 10-tool-error cap ends the task.** (6 of 20 failed tasks, 30%)
- tau2's full-duplex orchestrator stops a task after 10 tool errors (`max_errors: int = 10`,
  `$TAU2/src/tau2/orchestrator/full_duplex_orchestrator.py:72`).
- Lookups on every fragment, plus tau2's own instruction to re-ask for spelling after each failure
  (§5.3), use this up quickly.
- **6 tasks ended as `too_many_errors`: 9, 13, 18, 24, 25, 29.**

### 4.3 Outcomes

- Terminations: `user_stop` 23, `too_many_errors` 6, `agent_stop` 2.
- 11 passes: 0, 1, 2, 4, 5, 6, 10, 26, 27, 28, 30.
  - 10 of them need no write action (a refusal, a no-op, or an information-only request).
  - Tasks 6 and 28 ended with a transfer to a human.
- Of 19 tasks that need a write action, only **task 30** passed.
- Failures after successful authentication, which are ordinary agent mistakes:

| Task | What went wrong |
|---|---|
| 7 | Lost the authentication again (upper-case) and never performed the cancel or flight change |
| 11 | Looped over the same lookups and searches, never updated the reservation |
| 12 | Found the reservation after an ASR miss (`IBYAX` → `YAX4DR`) but made no change |
| 20 | Never asked for the user ID; looped on flight searches and never booked |

### 4.4 Per-task table (tasks 0–30)

"Text" columns: passes out of 4 trials in the text-2-text runs (§7.3). "ASR heard ID": the correct user
ID appears in the joined ASR transcript. "FE passed ID": it appears (any case) in a frontend `delegation`
query. "Authenticated": a `get_user_details` call with the exact correct ID succeeded.

| Task | Voice reward | Termination | Text paired (4 trials) | Text backend-only (4 trials) | Expected user ID | ASR heard ID | FE passed ID (any case) | Authenticated | Tool errors | Expected write actions | Write actions done | First failed user-ID lookups | ASR segments | Duration (s, sim) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 1.0 | user_stop | 4/4 | 4/4 | `emma_kim_9957` | - | - | - | 5 | (none) | - | `emm_kim_99`; `EMMA_KIM_9957`; `_AI_9957` | 29 | 317 |
| 1 | 1.0 | user_stop | 2/4 | 2/4 | `raj_sanchez_7340` | - | - | - | 3 | (none) | - | `RAJUS4734`; `_SANCHEZ_7340`; `RAJ_FANDEZ_340` | 18 | 186 |
| 2 | 1.0 | user_stop | 4/4 | 4/4 | `noah_muller_9847` | - | - | - | 6 | (none) | - | `OAHLLER987`; `O_A_MULER_9847`; `O.A.MULER 9847` | 59 | 653 |
| 3 | 0.0 | user_stop | 3/4 | 4/4 | `anya_garcia_5901` | Y | - | - | 9 | (none) | - | `MSA_EARCI_5901`; `MSA_GRCIA_5901` | 42 | 381 |
| 4 | 1.0 | user_stop | 4/4 | 4/4 | `sophia_silva_7557` | Y | Y | - | 9 | (none) | - | `Sophia_Silva_7557`; `OPHIA_SILVA_7557` | 27 | 257 |
| 5 | 1.0 | user_stop | 4/4 | 4/4 | `mei_brown_7075` | - | - | - | 1 | (none) | - | `May_brown_7075` | 10 | 99 |
| 6 | 1.0 | agent_stop | 4/4 | 4/4 | `sophia_taylor_9065` | Y | Y | - | 0 | (none) | transfer_to_human_agents | — | 17 | 160 |
| 7 | 0.0 | user_stop | 1/4 | 1/4 | `daiki_muller_1116` | Y | - | Y | 6 | update_reservation_flights, cancel_reservation | - | `AIKI_MULLER_116`; `daiki_muer_1116`; `DAIKI_MULLER_1116` | 22 | 339 |
| 8 | 0.0 | user_stop | 3/4 | 3/4 | `sophia_silva_7557` | Y | - | - | 5 | book_reservation | - | `_Silva_7557` | 38 | 353 |
| 9 | 0.0 | too_many_errors | 4/4 | 4/4 | `aarav_ahmed_6699` | Y | Y | - | 10 | (none) | - | `AAVAV_AHMED_6699`; `AAVAV_AHME_6699`; `AARAB_AHMED_6699` | 32 | 393 |
| 10 | 1.0 | user_stop | 4/4 | 4/4 | `liam_khan_2521` | Y | - | Y | 1 | (none) | - | — | 27 | 290 |
| 11 | 0.0 | user_stop | 3/4 | 3/4 | `james_patel_9828` | Y | Y | Y | 4 | update_reservation_flights | - | `James_Patel_9828`; `Jam__9828`; `AMES_4_Patel_9828` | 51 | 648 |
| 12 | 0.0 | user_stop | 3/4 | 3/4 | `chen_lee_6825` | Y | - | Y | 2 | update_reservation_baggages | - | — | 18 | 221 |
| 13 | 0.0 | too_many_errors | 4/4 | 4/4 | `james_lee_6136` | Y | - | - | 10 | transfer_to_human_agents | - | `jam_ef_lee_6136`; `J.A.M.EF_LEE_6136`; `J.A.M.EF_6136` | 19 | 219 |
| 14 | 0.0 | user_stop | 2/4 | 2/4 | `mohamed_silva_9265` | - | - | - | 2 | cancel_reservation, book_reservation | - | `Muhammad_Silva_9265`; `Muhammad_Shilva_9265` | 17 | 213 |
| 15 | 0.0 | user_stop | 3/4 | 3/4 | `aarav_garcia_1177` | - | - | - | 8 | update_reservation_flights | - | `Arav_Garcia_1177` | 22 | 205 |
| 16 | 0.0 | user_stop | 3/4 | 4/4 | `aarav_garcia_1177` | - | - | - | 2 | update_reservation_flights | - | `AARAV_GCI_1177` | 8 | 139 |
| 17 | 0.0 | user_stop | 2/4 | 4/4 | `omar_rossi_1241` | - | - | - | 5 | update_reservation_flights, update_reservation_passengers, update_reservation_baggages | - | `OMAR_R_1241`; `OMOSSI1241` | 19 | 273 |
| 18 | 0.0 | too_many_errors | 2/4 | 4/4 | `omar_davis_3817` | Y | Y | - | 10 | update_reservation_flights | - | `omar_davis_LU817` | 40 | 351 |
| 19 | 0.0 | user_stop | 4/4 | 4/4 | `olivia_gonzalez_2305` | Y | Y | - | 5 | cancel_reservation | - | `Olivia_Gonzalez_2305`; `Olivia_Gonzalez_Zero5`; `Olivia_L_easy_2305` | 18 | 298 |
| 20 | 0.0 | user_stop | 2/4 | 2/4 | `mia_li_3668` | - | - | - | 0 | book_reservation | - | — | 22 | 298 |
| 21 | 0.0 | user_stop | 2/4 | 4/4 | `sofia_kim_7287` | - | - | - | 6 | update_reservation_flights, update_reservation_baggages | - | `Sophia_Kim_7287`; `So_f_KIM_7287` | 27 | 256 |
| 22 | 0.0 | user_stop | 3/4 | 4/4 | `omar_rossi_1241` | - | - | - | 3 | update_reservation_flights, update_reservation_passengers, update_reservation_baggages | - | `OMAR_R.O._1241`; `OMAR_R.O._1_1241`; `O MAR_R.O._1_1241` | 15 | 183 |
| 23 | 0.0 | user_stop | 0/4 | 1/4 | `mohamed_silva_9265` | - | - | - | 3 | cancel_reservation, book_reservation | - | `MO`; `MOHAMED_ILV_A_9265`; `MOHAME.UNDERSCORESLV.A.926` | 33 | 306 |
| 24 | 0.0 | too_many_errors | 3/4 | 3/4 | `mia_kim_4397` | Y | Y | - | 10 | book_reservation | - | `MIA_KIM_4397` | 19 | 200 |
| 25 | 0.0 | too_many_errors | 3/4 | 4/4 | `ivan_muller_7015` | - | - | - | 10 | book_reservation | - | `IDA_MULLER_7015` | 22 | 211 |
| 26 | 1.0 | user_stop | 4/4 | 4/4 | `amelia_sanchez_4739` | - | - | - | 9 | (none) | - | `AMELI_SANEV_4739`; `NLIA_SAFE_Z_4739`; `Lia_ANBHEZ_4739` | 43 | 445 |
| 27 | 1.0 | user_stop | 4/4 | 4/4 | `ethan_martin_2396` | - | - | - | 3 | (none) | - | `B_M_A_`; `EPHAN_MAR_EIN_2396` | 45 | 383 |
| 28 | 1.0 | agent_stop | 4/4 | 4/4 | `amelia_rossi_1297` | Y | Y | Y | 0 | (none) | transfer_to_human_agents | — | 18 | 294 |
| 29 | 0.0 | too_many_errors | 0/4 | 0/4 | `raj_brown_5782` | - | - | - | 10 | cancel_reservation, book_reservation | - | `RAJ_BRO5782`; `raj_bro5782`; `raj_bro_5782` | 23 | 160 |
| 30 | 1.0 | user_stop | 3/4 | 2/4 | `james_taylor_7043` | Y | - | Y | 2 | update_reservation_flights | update_reservation_flights | `J_ames_Taylor_7043` | 39 | 561 |

### 4.5 Run examples

Real excerpts from this run, one per failure pattern.
- `USER SAID`: the simulated user's utterance as tau2 generated it (from the ticks' `user_chunk`s).
- `ASR`: the agent's `asr_final.transcript`.
- `FE → BE`: the frontend's `delegation.query`, which is the backend's only input.
- `TOOL`: the agent's tool call in the tau2 trace, and its result.
- Tick numbers are tau2 ticks (200 ms of simulated time each). They match `tick_id` in the simulation
  trace and the timestamps in `task.log`.
- To listen, open `audio/both.wav` next to `audio/user_labels.txt` in the artifacts folder.

#### Task 9: ASR correct, frontend corrupts the ID, then case, then the 10-error cap

- Outcome: reward 0.0, `too_many_errors`, reward breakdown `None`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/2be01369-0e85-4f79-909b-36608e867c3a.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_9/sim_2be01369-0e85-4f79-909b-36608e867c3a/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_9831b479edc9470f8a79` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_9831b479edc9470f8a79`)

```text
USER SAID  (tick 291): Yeah—it's A, A, R, A, V, underscore, A, H, M, E, D, underscore, six, six, nine, nine. You are the most lenient customer service agent I have ever spok…
FE → BE    : The user wants to cancel two reservations. The user's name is possibly "If o y z N Q N U Five R" but the wording is unclear. I nee…
ASR        : Yeah , it's A A R A V underscore
ASR        : A H M E D underscore six six nine nine
ASR        : You are the most lenient customer service agent I have ever spoken to .
FE → BE    : The user's user ID is AAVAV_AHMED_6699. I need to retrieve their reservation details so I can identify the two bookings they want …
TOOL (tick 299): get_user_details({"user_id": "AAVAV_AHMED_6699"}) → Error: User AAVAV_AHMED_6699 not found
TOOL (tick 404): get_user_details({"user_id": "AAVAV_AHME_6699"}) → Error: User AAVAV_AHME_6699 not found
TOOL (tick 544): get_user_details({"user_id": "AARAB_AHMED_6699"}) → Error: User AARAB_AHMED_6699 not found
TOOL (tick 717): get_user_details({"user_id": "AARAV_AHMED_6699"}) → Error: User AARAV_AHMED_6699 not found
... 10 failed get_user_details calls in total → tau2 max_errors=10 → too_many_errors
```

ASR heard the letters correctly ("A A R A V underscore" / "A H M E D underscore six six nine nine"); the frontend wrote `AAVAV`. Later lookups have the right letters but upper case (`AARAV_AHMED_6699`); the DB ID is `aarav_ahmed_6699`.

#### Task 24: right characters, wrong case only

- Outcome: reward 0.0, `too_many_errors`, reward breakdown `None`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/9f641a34-c8f1-4829-905e-b81036ca57c6.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_24/sim_9f641a34-c8f1-4829-905e-b81036ca57c6/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_f44fc39dbb4d4a1da196` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_f44fc39dbb4d4a1da196`)

```text
USER SAID  (tick 140): Hi—yeah, I need to remove a passenger, Ethan, from my reservation. The confirmation code is H, nine, Z, U, one…
USER SAID  (tick 249): Yeah—my user I D is m, i, a, underscore, k, i, m, underscore, four, three, nine, seven.
ASR        : The confirmation code is H nine V U one . C.
ASR        : Yeah, my user ID is MIA underscore KIM underscore four three nine seven
ASR        : It's MIA underscore K I.M.
TOOL (tick 150): get_reservation_details({"reservation_id": "H9VU1C"}) → Error: Reservation H9VU1C not found
TOOL (tick 260): get_user_details({"user_id": "MIA_KIM_4397"}) → Error: User MIA_KIM_4397 not found
TOOL (tick 441): get_reservation_details({"reservation_id": "H9VU1C"}) → Error: Reservation H9VU1C not found
```

The user ID reached the backend with the right characters; only the case was wrong (`MIA_KIM_4397` vs `mia_kim_4397`). The reservation code `H9ZU1C` was heard as "H nine V U one C".

#### Task 25: ASR letter error ("I, V, A, N" → "I D A N") repeated until the cap

- Outcome: reward 0.0, `too_many_errors`, reward breakdown `None`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/967f2970-55ed-4dd8-905c-be1ec0ed9606.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_25/sim_967f2970-55ed-4dd8-905c-be1ec0ed9606/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_0065f227183441dc8385` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_0065f227183441dc8385`)

```text
USER SAID  (tick 235): My user I D is I, V, A, N, underscore, M, U, L, L, E, R, underscore, seven, zero, one, five. Can you try that again?
ASR        : Ivan Motors and my user ID I D A N underscore
FE → BE    : The user wants to update the flight information on their reservation for their friend Ivan Mullers user ID IDA_MULLER_7015, keepin…
FE → BE    : The user wants to update the flight information on their reservation for their friend Ivan Mullers user ID IDA_MULLER_7015, keepin…
ASR        : My user ID , your IVA N underscore.
FE → BE    : The user wants to update the flight information on their reservation for their friend Ivan Mullers user ID IDA_MULLER_7015, keepin…
TOOL (tick 145): get_user_details({"user_id": "IDA_MULLER_7015"}) → Error: User IDA_MULLER_7015 not found
TOOL (tick 167): get_user_details({"user_id": "IDA_MULLER_7015"}) → Error: User IDA_MULLER_7015 not found
... 10 failed calls, all `IDA_MULLER_7015` → too_many_errors
```

#### Task 13: fragmented spelling, and a reservation code with a dropped letter

- Outcome: reward 0.0, `too_many_errors`, reward breakdown `None`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/a91eab17-604b-42c9-848d-2a078d2eeff8.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_13/sim_a91eab17-604b-42c9-848d-2a078d2eeff8/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_dc4497dd2a224ddebc69` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_dc4497dd2a224ddebc69`)

```text
USER SAID  (tick 219): Yeah—my user I D is J, A, M, E, S, underscore, L, E, E, underscore, six, one, three, six.
USER SAID  (tick 545): User I D: J, A, M, E, S, underscore, L, E, E, underscore, six, one, three, six.  Confirmation code: X, E, W, R, D, nine.
ASR        : E W R D nine
ASR        : My user ID is J .A.M.
ASR        : E F underscore L E E underscore six one three six
ASR        : YA J.
ASR        : Mes underscore
ASR        : E W R D nine
TOOL (tick 127): get_reservation_details({"reservation_id": "EWRD9"}) → Error: Reservation EWRD9 not found
TOOL (tick 244): get_reservation_details({"reservation_id": "EWRD9"}) → Error: Reservation EWRD9 not found
TOOL (tick 250): get_user_details({"user_id": "jam_ef_lee_6136"}) → Error: User jam_ef_lee_6136 not found
TOOL (tick 286): get_reservation_details({"reservation_id": "EWRD9"}) → Error: Reservation EWRD9 not found
TOOL (tick 304): get_user_details({"user_id": "J.A.M.EF_LEE_6136"}) → Error: User J.A.M.EF_LEE_6136 not found
```

The confirmation code `XEWRD9` lost its first letter ("E W R D nine"); "J, A, M, E, S" became "J .A.M." + "E F".

#### Task 0: one ID split into many turns; each fragment becomes a new frontend request

- Outcome: reward 1.0, `user_stop`, reward breakdown `{'DB': 1.0, 'COMMUNICATE': 1.0}`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/f62aa2f7-55ca-4ce7-bda8-fe5ea8a52c6b.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_0/sim_f62aa2f7-55ca-4ce7-bda8-fe5ea8a52c6b/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_51c068b27c9f4e7fbe91` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_51c068b27c9f4e7fbe91`)

```text
ASR        : For my user ID is Emma underscore Tim. Unders
FE → BE    : The user wants to cancel their reservation and has provided their user ID as Emma_Tim. I need to retrieve their reservation detail…
ASR        : Nine five seven
FE → BE    : The user wants to cancel their reservation and has provided their user ID as Emma_Tim_Underscore_Nine_Five_Seven.
ASR        : E M .M.
ASR        : Underscore K I M underscore nine nine.
ASR        : Let's go.
ASR        : 
ASR        : 
FE → BE    : The user wants to cancel their reservation and has provided their user ID as E M M Underscore K I M underscore Nine Nine.
ASR        : E M M A underscore K I M
ASR        : Under
```

The task still passes because its correct outcome is no DB change (the cancellation must be refused), so authentication was never needed.

#### Task 11: authenticated, then the stateless backend loops

- Outcome: reward 0.0, `user_stop`, reward breakdown `{'DB': 0.0, 'COMMUNICATE': 0.0}`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/0db484e6-a88c-4771-b613-c24959a11772.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_11/sim_0db484e6-a88c-4771-b613-c24959a11772/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_15b67c9a45834b8db942` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_15b67c9a45834b8db942`)

```text
12×  get_reservation_details({"reservation_id": "GV1N64"})
 5×  search_direct_flight({"date": "2024-05-19", "destination": "DEN", "origin": "LAS"})
 5×  search_direct_flight({"date": "2024-05-20", "destination": "LAS", "origin": "DEN"})
 1×  get_user_details({"user_id": "James_Patel_9828"})
 1×  get_user_details({"user_id": "Jam__9828"})
```

The backend starts each turn with an empty history, so it re-fetches the same reservation and repeats the same searches instead of making the expected `update_reservation_flights`.

#### Task 7: authenticated, then lost the authentication again (upper-case)

- Outcome: reward 0.0, `user_stop`, reward breakdown `{'DB': 0.0, 'COMMUNICATE': 0.0}`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/b571810c-7bbb-48be-a063-7abe40fc5c40.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_7/sim_b571810c-7bbb-48be-a063-7abe40fc5c40/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_5e18ed68aa044043a5e8` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_5e18ed68aa044043a5e8`)

```text
TOOL (tick 134): get_user_details({"user_id": "AIKI_MULLER_116"}) → Error: User AIKI_MULLER_116 not found
TOOL (tick 235): get_user_details({"user_id": "daiki_muer_1116"}) → Error: User daiki_muer_1116 not found
TOOL (tick 679): get_user_details({"user_id": "daiki_muller_1116"}) → OK
TOOL (tick 783): get_user_details({"user_id": "DAIKI_MULLER_1116"}) → Error: User DAIKI_MULLER_1116 not found
TOOL (tick 798): get_user_details({"user_id": "DAIKI_MULLER_1116"}) → Error: User DAIKI_MULLER_1116 not found
TOOL (tick 918): get_user_details({"user_id": "DAIKI_MULER_1116"}) → Error: User DAIKI_MULER_1116 not found
TOOL (tick 966): get_user_details({"user_id": "DAIKI_MULER_1116"}) → Error: User DAIKI_MULER_1116 not found
```

#### Task 30: the only write task that passed

- Outcome: reward 1.0, `user_stop`, reward breakdown `{'DB': 1.0, 'COMMUNICATE': 1.0}`
- Simulation trace: `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/ebd3950a-e968-4d0b-9177-75d4994cadd9.json`
- Artifacts: `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_30/sim_ebd3950a-e968-4d0b-9177-75d4994cadd9/` (`task.log`, `audio/both.wav`, `audio/user_labels.txt`, `llm_debug/`)
- Agent session: `sess_2b26a1fe32ad4d849b64` in `$AGENT/logs/fba_voice_events.jsonl` (`grep sess_2b26a1fe32ad4d849b64`)

```text
TOOL (tick 231): get_user_details({"user_id": "J_ames_Taylor_7043"}) → Error: User J_ames_Taylor_7043 not found
TOOL (tick 314): get_user_details({"user_id": "J_ames_Taylor_7043"}) → Error: User J_ames_Taylor_7043 not found
TOOL (tick 390): get_reservation_details({"reservation_id": "1N99U6"}) → OK
TOOL (tick 393): search_direct_flight({"date": "2024-05-19", "destination": "IAH", "origin": "LAS"}) → OK
TOOL (tick 407): get_user_details({"user_id": "james_taylor_7043"}) → OK
TOOL (tick 489): get_reservation_details({"reservation_id": "1N99U6"}) → OK
TOOL (tick 499): search_direct_flight({"date": "2024-05-19", "destination": "IAH", "origin": "LAS"}) → OK
TOOL (tick 509): get_user_details({"user_id": "james_taylor_7043"}) → OK
TOOL (tick 823): get_reservation_details({"reservation_id": "1N99U6"}) → OK
TOOL (tick 832): get_user_details({"user_id": "james_taylor_7043"}) → OK
TOOL (tick 849): get_flight_status({"date": "2024-05-19", "flight_number": "HAT284"}) → OK
TOOL (tick 854): search_direct_flight({"date": "2024-05-19", "destination": "IAH", "origin": "LAS"}) → OK
TOOL (tick 896): get_reservation_details({"reservation_id": "1N99U6"}) → OK
TOOL (tick 901): search_direct_flight({"date": "2024-05-19", "destination": "IAH", "origin": "LAS"}) → OK
TOOL (tick 917): get_user_details({"user_id": "james_taylor_7043"}) → OK
TOOL (tick 987): get_reservation_details({"reservation_id": "1N99U6"}) → OK
TOOL (tick 989): get_user_details({"user_id": "james_taylor_7043"}) → OK
TOOL (tick 1004): search_direct_flight({"date": "2024-05-19", "destination": "IAH", "origin": "LAS"}) → OK
TOOL (tick 1154): get_reservation_details({"reservation_id": "1N99U6"}) → OK
TOOL (tick 1160): search_direct_flight({"date": "2024-05-19", "destination": "IAH", "origin": "LAS"}) → OK
TOOL (tick 1162): get_user_details({"user_id": "james_taylor_7043"}) → OK
TOOL (tick 1185): update_reservation_flights({"cabin": "economy", "flights": [{"date": "2024-05-19", "flight_number": "HAT266"}, {"date": "2024-05-27", "fl…) → OK
```

Even this pass shows the stateless-backend pattern: `get_reservation_details(1N99U6)` and the same `search_direct_flight` were repeated 6 times before the change was made. Two early lookups failed on `J_ames_Taylor_7043`.

## 5. Does the OpenAI Realtime API integration cause issues?

tau2 drives the agent through its OpenAI Realtime provider
(`$TAU2/src/tau2/voice/audio_native/openai/provider.py`,
`$TAU2/src/tau2/voice/audio_native/openai/discrete_time_adapter.py`). The protocol works:
- sessions are stable since the keepalive fix;
- tool calls round-trip through tau2 (`tools.source: client`);
- the airline policy reaches the backend only, as in text (`instructions.apply_to: [backend]`,
  `$AGENT/src/prototypes/voice_frontend_backend_agent/config/voice_agent.yaml:105`).

These are the conditions the protocol carries that the text setup does not have.

### 5.1 Telephone audio (8 kHz μ-law)

- tau2 defaults to telephone audio: `audio_format = TELEPHONY_AUDIO_FORMAT` (`provider.py:331`) and
  `DEFAULT_TELEPHONY_RATE = 8000` (`$TAU2/src/tau2/config.py:104`).
- The agent decodes μ-law and resamples to its 16 kHz engine rate:
  `$AGENT/src/prototypes/voice_frontend_backend_agent/audio/formats.py`, `audio/g711.py`,
  `audio/resample.py`.
- Telephone bandwidth removes the high frequencies that tell apart B/V, D/E/P and F/S. This matches the
  letter errors in §4.2.
- This is τ³'s intended phone-call condition, the same for every Realtime provider. Keep it.

### 5.2 End-of-turn silence of 500 ms

- tau2's `session.update` (`provider.py:368`) sets `server_vad` with `silence_duration_ms: 500`.
- The agent takes the client's values: `honor_client_values: true`, `silence_duration_ms: 500`
  (`voice_agent.yaml:56-59`).
- Every pause over 0.5 s ends the user's turn, which causes the fragmentation in §4.2 (1).
- OpenAI's own speech-to-speech models get the same setting, but they keep hearing the whole
  conversation, so fragments cost them little. In this agent every fragment is a full frontend →
  backend → tool-call cycle.
- This is agent configuration, and changing it is a fair agent-side fix for a future campaign.

### 5.3 tau2's speech-to-speech instructions

- For the `openai` provider tau2 sends its speech-to-speech instruction, not its cascade instruction
  (`$TAU2/src/tau2/agent/discrete_time_audio_native_agent.py:100-113`; the cascade variant is at
  `:115`, chosen only when `provider_type == "cascaded"` at `:358`).
- That instruction tells the agent to ask for IDs letter by letter, and to ask again after every failed
  authentication. This drives the "please spell it out letter by letter" loops that use up the
  10-error cap.
- The agent adds its own `cascade_voice_addendum`
  (`$AGENT/src/prototypes/voice_frontend_backend_agent/config/prompts.voice.yaml:234`: "reassemble it
  into one identifier"). But instructions apply to the backend only, while the frontend is the model
  that assembles IDs from ASR fragments.

### 5.4 Other Realtime-path notes

- **Keepalive disconnects:** fixed (§3).
- **Token counts:** tau2 under-counts tokens of responses cut off by barge-in (check C4 WARN). The
  metrics script uses the agent's own counts.
- **tau2's clock runs at about half real time.** Compare tau2's `L_R`/`L_Y` only within the campaign,
  and use the report's *Mean realtime response latency* (runbook §7.3).

## 6. Recommendations for the next campaign

Agent-side changes. Don't apply them during the current campaign; a change means a new `CAMPAIGN`.

1. **Lower-case user IDs** before `get_user_details`, in the tool-argument path or the backend prompt
   (airline user IDs are lower-case). This alone would likely recover tasks 4, 9, 18, 19 and 24.
2. **Give the backend the verbatim transcript and its own history across turns.** Today it gets only
   the frontend's query and an empty `History()`.
3. **Stop ending the turn in the middle of spelling.** Options: `honor_client_values: false` with
   800–1000 ms silence in `tau3_eval.yaml`, holding the turn open after "underscore" or a single
   letter, or joining consecutive fragments.
4. **Read the ID back before looking it up, and never retry an ID that already failed**, so the
   10-error cap isn't burned.
5. **Improve ASR on spelled letters** (letter/digit vocabulary boosting, a spelling mode).
6. **Optional:** log the frontend/backend LLM prompts and raw responses. The agent logs extracted
   events only, not full LLM traces.

## 7. Artifact and log paths

### 7.1 Current (live) locations

| What | Path |
|---|---|
| tau2 run directory | `$TAU2/data/simulations/fba_voice_paired_airline_regular/` |
| Per-task traces: ticks (200 ms user/agent text, tool calls and results), reward breakdown, hallucination check | `$TAU2/data/simulations/fba_voice_paired_airline_regular/simulations/<sim_uuid>.json` |
| Combined results (written by tau2 as the run progresses) | `$TAU2/data/simulations/fba_voice_paired_airline_regular/results.json` |
| Per-task log | `$TAU2/data/simulations/fba_voice_paired_airline_regular/artifacts/task_<N>/sim_<uuid>/task.log` |
| Benchmark-side LLM calls (user simulator, backchannel/interruption decisions, judge), one JSON per call | `…/artifacts/task_<N>/sim_<uuid>/llm_debug/*.json` |
| Audio and timelines | `…/artifacts/task_<N>/sim_<uuid>/audio/both.wav`, `user_labels.txt`, `assistant_labels.txt`, `assistant_tool_calls_labels.txt` |
| Discarded hallucination attempts | `$TAU2/data/simulations/fba_voice_paired_airline_regular/hallucination_discarded/` (if present) |
| Console output, run window | `$TAU2/data/simulations/_consoles/fba_voice_paired_airline_regular.{log,start,end}` |
| Campaign name | `$TAU2/data/simulations/_consoles/CAMPAIGN` |
| Campaign launcher / status script | `$TAU2/data/simulations/_consoles/airline_campaign.sh`, `airline_status.sh` |
| Agent event log (ASR `asr_final.transcript`, frontend `delegation.query`, `backend_tool_calls`, `backend_final`, `agent_turn_done` usage/latency, barge-ins). Shared across runs; filter by `session_start.model == "pine-fba-voice-paired-airline-regular"` | `$AGENT/logs/fba_voice_events.jsonl` |
| Agent filler timing log (`log_only`, never spoken) | `$AGENT/logs/fba_filler.jsonl` |
| Agent container logs | `docker logs fba-voice` (ASR/TTS server: `docker logs nemotron-voice-agent-nemo-speech-1`) |
| I0 overrides (Hub user TTS/LLMs, keepalive fix) | `$TAU2/misc/prototypes/fba_voice_eval/tau2_ihub_overrides.py`, `tau2_ihub.py` |
| Metrics script (I2) | `$TAU2/misc/prototypes/fba_voice_eval/fba_voice_metrics.py` |
| Aborted earlier attempts | `$TAU2/data/simulations/_aborted_fba_voice_paired_airline_regular_0833/`, `$TAU2/data/simulations/_aborted_fba_voice_bo_airline_regular_0814/` and their `_consoles/_aborted_*` files |
| Smoke runs | `$TAU2/data/simulations/fba_voice_paired_airline_regular_smoke/`, `fba_voice_paired_mock_control/` |

### 7.2 Code referenced in this document

| What | Path |
|---|---|
| Backend gets an empty history each turn | `$AGENT/src/prototypes/text_frontend_backend_agent/agent.py:106-124` (`History()` at `:117`) |
| Backend input handling | `$AGENT/src/prototypes/text_frontend_backend_agent/backend.py` (`_apply_input`) |
| Voice agent config (turn detection, instructions) | `$AGENT/src/prototypes/voice_frontend_backend_agent/config/voice_agent.yaml:52-59`, `:104-110` |
| tau3 eval profile | `$AGENT/src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval.yaml` |
| Cascade voice addendum | `$AGENT/src/prototypes/voice_frontend_backend_agent/config/prompts.voice.yaml:234` |
| Audio decode/resample | `$AGENT/src/prototypes/voice_frontend_backend_agent/audio/{formats,g711,resample}.py` |
| tau2 Realtime client (connect, `session.update`, telephone format) | `$TAU2/src/tau2/voice/audio_native/openai/provider.py:201`, `:331`, `:368` |
| tau2 voice agent instructions | `$TAU2/src/tau2/agent/discrete_time_audio_native_agent.py:100-135`, `:358` |
| tau2 tool-error cap | `$TAU2/src/tau2/orchestrator/full_duplex_orchestrator.py:72` |
| User simulator spelling rules | `$TAU2/data/tau2/user_simulator/simulation_guidelines_voice.md:17`, `simulation_guidelines_voice_tools.md:22` |
| Airline tasks (expected actions, user IDs) | `$TAU2/data/tau2/domains/airline/tasks.json` |
| Text-2-text adapter | `$TAU2/tau2-fba/` (`run_fba_eval.py`, `tau2_fba/`) |

### 7.3 Text-2-text baseline used for comparison

| What | Path |
|---|---|
| Text paired, airline, 4 trials (Pass^1 0.71) | `$TAU2/data/simulations/fba_paired_airline_base_4trials/` (`results.json`, `fba_report.md`, `fba_per_task.csv`) |
| Text backend-only, airline, 4 trials (Pass^1 0.79) | `$TAU2/data/simulations/fba_backend_only_airline_base_4trials/` |
| Text summary report | `$TAU2/misc/prototypes/results/airline_base_4trials.md` |

### 7.4 Where artifacts will be written after the run (runbook §7 and §9)

Metrics (runbook §7), under `$TAU2`:

| What | Path |
|---|---|
| Pass^1 and tau2 metrics (§7.1) | `$TAU2/data/simulations/_metrics/fba_voice_paired_airline_regular/agent_metrics.json` |
| tau2 interaction metrics: latency, responsiveness, interrupts, selectivity (§7.1) | `$TAU2/data/simulations/_metrics/fba_voice_paired_airline_regular/interaction_metrics.json` |
| Agent setup record (§7.2) | `$TAU2/data/simulations/_metrics/_setup/paired.json` |
| Full report, paired arm only (§7.2) | `$TAU2/data/simulations/_metrics/fba_voice_airline_regular/`: `fba_voice_report.md`, `fba_voice_results_table.{md,csv}`, per-task/per-turn CSVs, session-to-task join, checks C1–C8 |

Archive (runbook §9), in `$DUMP/tau-3-voice/2026-09-24_08-47-49Z_fba-voice/`:

```
$DUMP/tau-3-voice/2026-09-24_08-47-49Z_fba-voice/
├── README.md                                  run card (paired only, keepalive override, aborted run noted)
├── _reports/                                  copy of $TAU2/data/simulations/_metrics/ (§7.2 report, CSVs, JSON)
└── fba_voice_paired_airline_regular/
    ├── tau2/fba_voice_paired_airline_regular/  results.json, simulations/, artifacts/ (audio, task.log, llm_debug)
    ├── tau2/fba_voice_paired_airline_regular.{log,start,end}   console output and run window
    ├── metrics/                                agent_metrics.json, interaction_metrics.json
    ├── agent/events.jsonl                      this run's agent sessions only
    ├── agent/filler.jsonl                      this run's filler timing records
    ├── agent/raw/                              full fba_voice_events.jsonl / fba_filler.jsonl as of archiving
    ├── agent/docker_logs.txt                   fba-voice logs for the run window
    ├── agent/nemo_speech_logs.txt              ASR/TTS server logs for the run window
    ├── agent/config/                           voice_agent.yaml, prompts.voice.yaml, profiles/*.yaml, agent.yaml, services.local.yaml
    ├── agent/container_inspect.json            secrets redacted
    └── provenance/                             commits, git status, uncommitted diffs of both repos,
                                                tau2 .env key names, endpoints, I0 files, agent LLM env
```

The dump repo will not be committed or pushed without the user's go-ahead.

## 8. How the numbers in §4 were computed

- **Voice outcomes:** `reward_info`, `termination_reason` and tick-level tool calls/results from
  `simulations/*.json`.
- **Text baselines:** per-task rewards from the two text `results.json` files.
- **User ID:** taken from each task's `user_scenario` in `tasks.json`.
- **"ASR heard ID":** all `asr_final.transcript`s of the task's agent session, joined, lower-cased, with
  number words and "underscore" mapped to characters.
- **Session-to-task join:** agent sessions tagged `pine-fba-voice-paired-airline-regular`, matched to a
  simulation by its start/end time; for tasks with a hallucination re-run, the last session.
- **"Case-only" failure:** the failed `user_id` equals the correct ID when lower-cased.
- **Cause shares (§4.2):** computed from the agent event log (`backend_tool_calls` joined to
  `tool_output_in` by `call_id`) for each task's final session. A failed call counts as "ASR had the
  correct ID" when the ID appears in the normalised join of all `asr_final` transcripts timestamped
  before the call. Labels are applied in the table's order, so a repeat of a case-only ID counts as
  case. This gives 151 failed calls; the tau2-trace count in §4.1 differs slightly (it counts
  `get_user_details` only, across the tau2 ticks). A failed task's primary cause:
  - authenticated at some point → agent errors;
  - otherwise, any case-only failure → case;
  - otherwise, the correct ID in the ASR transcript → fragments/frontend;
  - otherwise → ASR.
  Script: `$TAU2/misc/prototypes/observations/attribute_failures.py` (run from `$TAU2`). Rerun it
  after the run ends to update the numbers.

## 9. Final results (all 50 tasks, added after the run ended at 13:47:28Z)

§1–§4 were written at 31/50 tasks. The final numbers confirm the same picture, somewhat stronger.

| Metric | Final (50 tasks) | At 31 tasks |
|---|---|---|
| Pass^1 | **0.36** (18/50) | 0.35 (11/31) |
| Tasks needing a write action that passed | **2 / 27** (tasks 30, 33) | 1 / 19 |
| Passes that needed no write action | 16 / 18 | 10 / 11 |
| `too_many_errors` endings | **15** | 6 |
| Terminations | `user_stop` 31, `agent_stop` 4, `too_many_errors` 15 | |
| Infra errors / disconnects | 0 / 0 | 0 / 0 |
| Text-2-text reference (all 50 tasks) | paired 0.71, backend-only 0.79 | |

tau2 interaction metrics:

| Metric | Value |
|---|---|
| L_R (response latency) | 4.21 s |
| L_Y (yield latency) | 0.82 s |
| R_R / R_Y (response / yield rate) | 81% / 86% |
| I_A (agent interrupts user) | 40% |
| S_BC / S_VT / S_ND (selectivity) | 42% / 48% / 44% |

Agent-side metrics (I2 report):

| Metric | Value |
|---|---|
| Mean realtime response latency | 7.33 s (p90 15.43) |
| Backend turn latency | 4.78 s (p90 10.99) |
| Frontend per-turn latency | 1.18 s |
| Filler voice latency (projected) | 2.70 s |
| Tokens per task | frontend 35 352, backend 126 059 |
| Turns cancelled by barge-in | 779 of 1241 turns |

Checks: C1–C3, C6, C7 PASS. C4 WARN (barge-in token undercount, expected). C5 WARN (1
`frontend_contract_violation`). C8 WARN (15 `too_many_errors`).

**Primary cause of the 32 failed tasks** (same method as §4.2; `attribute_failures.py`):

| Primary cause | Tasks | Share | Task IDs |
|---|---|---|---|
| ASR never produced the correct ID | 13 | 41% | 14, 15, 16, 17, 21, 22, 23, 25, 29, 37, 39, 44, 47 |
| Only upper case (right characters) | 10 | 31% | 9, 18, 19, 24, 32, 34, 40, 42, 45, 46 |
| ASR heard the ID; lost in turn fragments / frontend | 5 | 16% | 3, 8, 13, 35, 38 |
| Authenticated, then agent errors | 3 | 9% | 7, 11, 12 |
| Never asked for the ID | 1 | 3% | 20 |

**Failed tool calls (277):**

| Cause | Count | Share |
|---|---|---|
| Retry of an ID that had already failed | 87 | 31% |
| Incomplete ID (turn fragment) | 70 | 25% |
| ASR misheard letters | 60 | 22% |
| Upper case only | 45 | 16% |
| Frontend garbled an ID the ASR had right | 13 | 5% |
| Other | 2 | 1% |

**Contributing causes, of 32 failed tasks:**

| Contributing cause | Failed tasks | Share |
|---|---|---|
| Retries of an ID that already failed | 25 | 78% |
| ASR letter errors | 23 | 72% |
| Incomplete IDs from turn fragments | 22 | 69% |
| Ended by the 10-error cap | 15 | 47% |
| Upper case | 12 | 38% |
| Frontend garbling | 7 | 22% |

The upper-case problem grew in the second half of the run: it is now the primary cause of 31% of
failed tasks, up from 20%. Lower-casing user IDs (§6, item 1) remains the cheapest fix. It would have
unblocked authentication in 10 of the 32 failed tasks.

**Final artifact locations.** The archive has been written (§7.4). The run card is
`$DUMP/tau-3-voice/2026-09-24_08-47-49Z_fba-voice/README.md`, and a copy of this document is in its
`observations/` folder.
- `agent/events.jsonl` holds this run's 56 sessions only. The 11 sessions from the aborted
  07:31–08:33 attempt share the model tag and were excluded by start time.
- The dump repo has not been committed.
