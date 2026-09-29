# Failure analysis and comparison: τ³ voice, Frontend/Backend Agent, paired, airline, `regular`, **800 ms end-of-turn silence**

**Campaign:** `2026-09-25_13-25-31Z_fba-voice` (run `fba_voice_paired_airline_regular_sil800`, 2026-09-25 13:25:36 → 19:41:09 UTC).
**Compared with:**
- the voice campaign `2026-09-24_08-47-49Z_fba-voice` (the same agent with a 500 ms silence);
- the two text-2-text tau2 runs of the same agent (paired, and backend-only; 4 trials each).

**Written:** 2026-09-26, after both voice runs finished (all 50 tasks).

Paths use the runbook's variables:

```
TAU2  = /localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar
AGENT = /localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar
DUMP  = /localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump
NEW   = $DUMP/tau-3-voice/2026-09-25_13-25-31Z_fba-voice          (this campaign)
OLD   = $DUMP/tau-3-voice/2026-09-24_08-47-49Z_fba-voice          (500 ms campaign)
```

The 500 ms campaign's own analysis, with the root-cause discussion, the Realtime-API review and
earlier trace examples, is `$OLD/observations/2026-09-24_fba-voice-paired-airline-regular.md`. This
document doesn't repeat it; it covers what changed.

---

## 1. Summary

- **Pass^1 is unchanged: 0.36 (18/50) at both 800 ms and 500 ms.** Text-2-text scores 0.71 (paired)
  and 0.79 (backend-only) on the same 50 tasks. The voice gap is still about 35–43 points.
- **The 800 ms silence does what it was meant to do: much less turn splitting.**
  - Agent turns cut off because the user kept talking fell from 779 to 354.
  - ASR segments per task fell from 28.3 to 18.1.
  - The ASR now captures the full user ID in more tasks (26 → 37 of 50), and the frontend passes it
    on in more tasks (19 → 27).
- **The gain is lost further down.** The ID now arrives in one piece but in the **wrong case**:
  - Case-only lookup failures rose from 45 to 79 calls.
  - Upper case became the main cause of 14 of the 32 failed tasks (10 before).
- **More tasks now log in and then fail on the agent side** (3 → 7). The backend is stateless per
  turn, so it repeats lookups and writes, and misses parts of multi-part requests.
- **Booking-change tasks still almost never pass.** Of the 27 tasks that need a write action, 1 passed
  (task 13, a transfer to a human), against 2 at 500 ms (tasks 30 and 33).
- **Fewer tasks hit tau2's 10-tool-error limit** (15 → 8), because there are fewer retries of IDs that
  had already failed (87 → 54 calls).
- **Latency didn't suffer.** Endpointing is 280 ms longer (0.59 → 0.87 s), but L_R improved slightly
  (4.21 → 4.06 s) because fewer turns are cancelled and restarted. Selectivity for backchannels
  improved a lot (S_BC 42% → 75%).
- **Harness:** both runs were clean. Infra errors 0, disconnects 0, failed user/judge/TTS calls 0.
- **Conclusion:** keep 800 ms. The score won't move until:
  1. user IDs are lower-cased before the lookup (would unblock login in 14 of the 32 failures);
  2. the backend gets the conversation history (7 failures, and most of the repeated calls).

## 2. What was compared

| Item | 500 ms run (2026-09-24) | 800 ms run (2026-09-25) |
|---|---|---|
| Run name | `fba_voice_paired_airline_regular` | `fba_voice_paired_airline_regular_sil800` |
| Agent model tag | `pine-fba-voice-paired-airline-regular` | `pine-fba-voice-paired-airline-regular-sil800` |
| Turn detection | `silence_duration_ms` 500 (tau2's value, `honor_client_values: true`) | **800 (agent value, `honor_client_values: false`)** |
| Agent code / prompts / models | commit `4b9bbc8`; FE `nemotron-3.5-lightning` (no reasoning), BE `nemotron-3-ultra` (reasoning 1024) | same; only `profiles/tau3_eval.yaml` changed (uncommitted) |
| ASR / TTS | `nemotron-speech-streaming-en-0.6b` / `magpie-tts-multilingual` | same |
| User simulator | gpt-5.2 LLM, gpt-4o-mini-tts voice, gpt-4.1 decisions (I0), `regular` speech | same |
| Harness | tau2 + I0 with keepalive override; 1 trial, concurrency 1 | same (tau2 commit `7c7a6e4` holds the same I0) |
| Wall time | 5 h 00 min | 6 h 16 min (10 hallucination re-runs of the simulated user, against 6) |

Text-2-text baselines: `$TAU2/data/simulations/fba_paired_airline_base_4trials` and
`$TAU2/data/simulations/fba_backend_only_airline_base_4trials` (airline `base`, 50 tasks × 4 trials,
same frontend/backend models, typed user).

## 3. Headline comparison

| Metric | Text paired | Text backend-only | Voice 500 ms | Voice 800 ms |
|---|---|---|---|---|
| **Pass^1** | 0.71 | 0.79 | 0.36 | **0.36** |
| Tasks that need a write action, passed | – | – | 2 / 27 | 1 / 27 |
| Passes that needed no write action | – | – | 16 / 18 | 17 / 18 |
| Terminations: user / agent / 10-error cap | – | – | 31 / 4 / 15 | 38 / 4 / **8** |
| Infra errors / disconnects | 0 | 0 | 0 / 0 | 0 / 0 |

tau2 interaction metrics:

| Metric | 500 ms | 800 ms | Better when |
|---|---|---|---|
| L_R response latency | 4.21 s | 4.06 s | lower |
| L_Y yield latency | 0.82 s | 0.80 s | lower |
| R_R response rate | 81% | 79% | higher |
| R_Y yield rate | 86% | 87% | higher |
| I_A agent interrupts user | 40% | 37% | lower |
| S_BC backchannel selectivity | 42% | **75%** | higher |
| S_VT vocal-tic selectivity | 48% | 59% | higher |
| S_ND non-directed-speech selectivity | 44% | 44% | higher |

Agent-side metrics (I2 report):

| Metric | 500 ms | 800 ms |
|---|---|---|
| Endpointing (end of speech to turn commit), mean | 0.59 s | **0.87 s** |
| Mean realtime response latency (p90) | 7.33 s (15.43) | 6.94 s (13.82) |
| Backend turn latency, mean (p90) | 4.78 s (10.99) | 4.37 s (9.35) |
| Frontend LLM per turn | 1.18 s | 1.02 s |
| Filler voice latency, projected | 2.70 s | 2.71 s |
| Turns: answered / cancelled by the user talking | 434 / **779** | 452 / **354** |
| Tokens per task, frontend / backend | 35,352 / 126,059 | 38,237 / 123,500 |
| Report checks | C1–C3, C6, C7 PASS; C4, C5, C8 WARN | C2, C3, C5–C7 PASS; C4, C8 WARN; C1 FAIL (matching artifact, §8) |

## 4. The authentication funnel

Almost every voice failure is at login, so the funnel shows where the 800 ms change helped and where
it didn't. Counts are over the 50 tasks, using each task's final (scored) agent session.

| Stage | Voice 500 ms | Voice 800 ms | Text |
|---|---|---|---|
| ASR heard the complete user ID at least once (fragments joined) | 26 | **37** | typed exactly |
| Frontend passed the correct ID (any case) to the backend | 19 | **27** | nearly all |
| `get_user_details` succeeded with the correct ID | 10 | 11 | nearly all |
| Task passed | 18 | 18 | 35.5 (paired) / 39.5 (backend-only), mean of 4 trials |

- **ASR → frontend:** better. With fewer fragments, the ID reaches the ASR, and then the frontend's
  query, in one piece more often.
- **Frontend → successful lookup:** worse. In 16 of the 27 tasks where the frontend passed the right
  ID, login still failed, against 9 of 19 at 500 ms. The frontend writes it as `Mia_Kim_4397` or
  `MIA_KIM_4397`, and the backend sends it as-is. The database IDs are lower case.
- **Logged in → pass:** worse. At 500 ms, 7 of the 10 tasks that logged in passed. At 800 ms, only 4 of
  11 did (6, 13, 31, 34); the other 7 failed on the agent side (§5.4, item 4).

## 5. Failure causes

Method: `attribute_failures.py` (§10), applied to both runs in the same way.

### 5.1 Main cause of each failed task (32 failed tasks in each run)

| Main cause | 500 ms | Share | 800 ms | Share | Change |
|---|---|---|---|---|---|
| ASR never produced the correct ID | 13 | 41% | **5** | 16% | −8 |
| Only upper case (the right characters) | 10 | 31% | **14** | 44% | +4 |
| Logged in, then agent errors | 3 | 9% | **7** | 22% | +4 |
| ASR heard the ID; lost in fragments or by the frontend | 5 | 16% | 5 | 16% | 0 |
| Never asked for the ID | 1 | 3% | 1 | 3% | 0 |

Task IDs:

| Main cause | 500 ms tasks | 800 ms tasks |
|---|---|---|
| ASR never correct | 14, 15, 16, 17, 21, 22, 23, 25, 29, 37, 39, 44, 47 | 1, 17, 21, 37, 44 |
| Upper case only | 9, 18, 19, 24, 32, 34, 40, 42, 45, 46 | 8, 18, 19, 20, 22, 23, 24, 25, 30, 32, 35, 42, 43, 47 |
| Logged in, then agent errors | 7, 11, 12 | 3, 5, 11, 12, 14, 29, 33 |
| Fragments / frontend | 3, 8, 13, 35, 38 | 0, 7, 15, 16, 39 |
| Never asked for the ID | 20 | 40 |

Eight tasks moved from "ASR never correct" at 500 ms to a later stage at 800 ms: 15, 16, 22, 23, 25,
29, 39 and 47. None of them passed. Five of them (22, 23, 25, 47, and 29 after logging in) now stop at
the case barrier or on the agent side.

### 5.2 Failed tool calls

| Cause | 500 ms | Share | 800 ms | Share |
|---|---|---|---|---|
| Retry of an ID that had already failed | 87 | 31% | **54** | 22% |
| Incomplete ID from a turn fragment | 70 | 25% | 68 | 27% |
| ASR misheard letters | 60 | 22% | **22** | 9% |
| Upper case only | 45 | 16% | **79** | 31% |
| Frontend garbled an ID the ASR had right | 13 | 5% | 28 | 11% |
| Other | 2 | 1% | 0 | 0% |
| **Total** | **277** | | **251** | |

### 5.3 Contributing causes (tasks with at least one such error, of the 32 failed tasks)

| Contributing cause | 500 ms | 800 ms |
|---|---|---|
| Retries of an ID that had already failed | 25 (78%) | 15 (47%) |
| ASR letter errors | 23 (72%) | 11 (34%) |
| Incomplete IDs from turn fragments | 22 (69%) | 20 (62%) |
| Ended by the 10-error cap | 15 (47%) | 8 (25%) |
| Upper case | 12 (38%) | 17 (53%) |
| Frontend garbling | 7 (22%) | 9 (28%) |

### 5.4 Reading the causes

1. **Turn splitting is lower but not gone.** Cut-off turns halved, yet incomplete-ID calls barely
   moved (70 → 68). The simulated user sometimes pauses longer than 800 ms between letter groups
   ("J underscore." … "Underscore seven three" … "Seven three four zero", task 1). The frontend still
   sends each piece to the backend as if it were the whole ID.
2. **Misheard letters dropped sharply** (60 → 22 calls, 13 → 5 tasks). Whole-ID utterances give the
   ASR, and the frontend's charitable reading, more context. This is the one clear win.
3. **Upper case is now the biggest single blocker.** The prompt tells the backend to read IDs back
   the way a person would, and the frontend capitalises names. Neither step lower-cases the ID for the
   lookup. This is a one-line fix in the agent: lower-case `user_id` before `get_user_details`, or say
   in the backend policy that IDs are lower case.
4. **The stateless backend causes the post-login failures.** It sees only the frontend's summary for
   each turn, so it:
   - re-fetches the same reservation every turn;
   - repeats writes: task 33 ran `update_reservation_flights` twice;
   - forgets part of a request: task 33 never did the baggage update;
   - loses the authenticated user between turns.
5. **The retry loop is weaker but still there.** Retries of known-bad IDs fell from 87 to 54 calls,
   mostly because fewer bad IDs were produced in the first place. There is still no read-back or
   "already tried" check.

## 6. Tasks that changed outcome

The pass count is the same, but the tasks that passed are not:

| | Tasks |
|---|---|
| Passed in both | 2, 4, 6, 10, 26, 27, 28, 31, 36, 41, 48, 49 (12) |
| **Gained at 800 ms** | 9, 13, 34, 38, 45, 46 (6) |
| **Lost at 800 ms** | 0, 1, 5, 30, 33, 43 (6) |

- **Gained:**
  - 9 and 45 ended with a correct `transfer_to_human_agents` (`agent_stop`), where at 500 ms they hit
    the error limit.
  - 34, 38 and 46 are no-write tasks where the agent found the reservation by its code, or stopped
    retrying, and so avoided the error limit.
  - 13 is the only write-action pass (a transfer); see §7.3.
- **Lost:**
  - 0, 1 and 43 are no-write tasks that passed at 500 ms and now hit the 10-error limit on user-ID
    retries. See §7.1 for task 0.
  - 5 logged in, then ran into the limit on a reservation code with a dropped character (§7.4).
  - 30 and 33 were the two write-action passes at 500 ms. 30 now never logs in (upper case, §7.5);
    33 logged in and changed the flights, but missed the baggage change (§7.6).
- **Gained and lost are almost all no-write tasks.** Their outcome depends mainly on whether the
  agent reaches the error limit before the user gives up. That is sensitive to the simulated user's
  wording and noise, which vary from run to run. With one trial per setting, a difference of 6 tasks
  in each direction is consistent with run-to-run variation. The cause shifts in §5 are larger and
  more consistent, so they are the more reliable signal.

## 7. Run examples (800 ms run)

For each example, with `R = $NEW/fba_voice_paired_airline_regular_sil800`:
- **Simulation:** `$R/tau2/fba_voice_paired_airline_regular_sil800/simulations/<sim>.json`
- **Audio, task log and LLM traces:** `$R/tau2/fba_voice_paired_airline_regular_sil800/artifacts/task_<N>/sim_<sim>/`, containing `audio/both.wav`, `task.log` and `llm_debug/`
- **Agent events:** `$R/agent/events.jsonl`; filter on the session ID.

The same files are also in `$TAU2/data/simulations/fba_voice_paired_airline_regular_sil800/` and
`$AGENT/logs/fba_voice_events.jsonl`. Transcripts below are the agent's ASR output (`asr_final`).
Backend tool calls are shown with their result.

### 7.1 Task 0: passed at 500 ms, lost at 800 ms. Split ID, misheard letters, retries, 10-error limit

- sim `8b5d169d-2d5e-4825-bcc6-501558d1f2a8`, session `sess_27cb3302e4f64bb7a361`
- Expected user ID `emma_kim_9957`. Result: reward 0, `too_many_errors`.
- For comparison, the 500 ms run's session was `sess_51c068b27c9f4e7fbe91` (sim `f62aa2f7-…`, in `$OLD`).

```
USER  HGLT three. / Underscore. / Eight. / Underscore nine nine five seven .
USER  M A underscore K im underscore nine nine five seven reason
CALL  get_user_details(MA_Kim_9957)        -> Error: not found
CALL  get_reservation_details(HGLT3)       -> Error: not found
CALL  get_user_details(EM_MA_KIM_9957)     -> Error: not found
CALL  get_user_details(lena_kim_9957)      -> Error: not found
CALL  get_user_details(emma_aim_9957) ×2, EMMA_AIM_9957, …   -> Error … (10-error limit)
```

- The ID still arrives in fragments at 800 ms.
- The reservation code `EHGLP3` comes out as "HGLT3": one letter dropped and one misheard.
- The ASR hears "Emma" as "M A", "EM MA", "Lena" and "Emma aim".
- The backend asks for the spelling again each turn, and retries IDs it has already tried.

### 7.2 Task 24: right characters, wrong case, until the limit (both runs)

- sim `710f9c31-4ca1-41df-ae90-22e3b68d0080`, session `sess_67603b742f7347ee8523`
- Expected `mia_kim_4397`. Result: reward 0, `too_many_errors` in both runs.

```
USER  … my user ID is Mia underscore Kim underscore four three nine se[ven]
FE    The user Mia Kim with user ID Mia_Kim_4397 wants to remove passenger Ethan from reservation H9Z1C.
CALL  get_user_details(Mia_Kim_4397)   -> Error: User Mia_Kim_4397 not found
USER  User ID MIA underscore KIM underscore four three nine seven reservation H nine ZU / One C
CALL  get_user_details(Mia_Kim_4397)   -> Error        (same ID again)
CALL  get_reservation_details(H9Z1C)   -> Error        (the code is H9ZU1C: "ZU" and "One C" were split, and the U was lost)
… repeated until the 10-error limit
```

- The ASR and the frontend have the ID exactly right apart from case.
- The upper-case failure is the most common single cause in this run (14 failed tasks).

### 7.3 Task 13: gained at 800 ms. The only write-action pass

- sim `abf23a78-a199-41dc-aa81-a66b95237231`, session `sess_8613b816295a41eb9dff`
- Expected `james_lee_6136`, and a correct `transfer_to_human_agents`. Result: reward 1, `agent_stop`.

```
USER  Underscore Lee underscore six one three six .           (the first name never reached the ASR)
CALL  get_user_details(_Lee_6136)       -> Error
USER  Yeah, it's J A M E S underscore L E E / Underscore six one three six .
CALL  get_user_details(JAMES_LEE_6136)  -> Error
CALL  get_user_details(james_lee_6136)  -> OK            (the backend tried lower case itself this time)
CALL  get_reservation_details(XEWRD9)   -> OK            (after "WRD9", "SEWR9")
USER  I'll pay up to a hundred bucks . If not possible , transfer. Me.
CALL  transfer_to_human_agents(...)     -> task passes
```

- The backend sometimes recovers from the case problem by trying lower case, but not reliably. It
  didn't in tasks 24, 30 and 47.

### 7.4 Task 5: lost at 800 ms. Logged in, then the stateless backend forgot the reservation

- sim `a13497d0-0bd2-40a3-b4b9-41a6049f4417`, session `sess_048d33c25a2f4925ac9e`
- Expected `mei_brown_7075`, no write. Result: reward 0, `too_many_errors`.

```
CALL  get_user_details(May_brown_7075) ×2, MEI_BROWN_7075        -> Error
USER  M E I underscore B R O W / Underscore seven zero seven five .
CALL  get_user_details(mei_brown_7075)          -> OK
CALL  get_reservation_details(3JA7XV)           -> OK   (flight HAT045, delayed)
AGENT I found your reservation 3JA7XV with flight HAT045 …
… later turns: the FE query only says "reservation code JA7X"
CALL  get_reservation_details(JA7X), (JA7XV), repeated  -> Error … (10-error limit)
```

- The backend had already found `3JA7XV`. On later turns it saw only the frontend's new summary,
  which held a misheard fragment ("JA7X"), so it looked that up again and again.
- This is the "logged in, then agent errors" cause, which grew from 3 to 7 tasks.

### 7.5 Task 30: a write-action pass at 500 ms, failed at 800 ms. Case, then the task was never finished

- sim `a9757ef0-b31b-442d-b001-be081c86e6a0`, session `sess_763e2dcecc284c199ab1`
- Expected `james_taylor_7043` and `update_reservation_flights` to a nonstop flight (the user also asks to remove a bag). Result: reward 0, `user_stop`, DB mismatch.
- The 500 ms pass was session `sess_2b26a1fe32ad4d849b64` (sim `ebd3950a-…`, in `$OLD`).

```
USER  James underscore Taylor underscore seven zero four          (split; the last digit is in the next turn)
CALL  get_user_details(James_Taylor_704)    -> Error
USER  Yeah , it's J am es underscore T A Y L O R underscore seven zero four three .
CALL  get_user_details(James_Taylor_7043)   -> Error            (right characters, wrong case)
CALL  get_user_details(J.A.M._Taylor_7043)  -> Error
USER  Uh do you want my reservation ID instead ? It's one N nine nine U six
CALL  get_reservation_details(1N99U6)       -> OK
… the flight change was never made
```

### 7.6 Task 33: a write-action pass at 500 ms, failed at 800 ms. Logged in, flights changed, bags missed

- sim `aed65a0a-90a4-49c4-9221-2d63aae88bd5`, session `sess_bb66bdfb804d49c4b208`
- Expected `yara_garcia_1905`; change both flights and the bags. Result: reward 0, `user_stop`, DB mismatch.
- This is the task that ran for 33 minutes of real time on its first attempt; that attempt was
  discarded by tau2's hallucination check.

```
USER  My reservation is HX D U.E.J.
CALL  get_reservation_details(HXDU.E.J), (HXDUEJ) ×2, (HXD U E J)   -> Error ×4 in one turn
USER  Yeah Yara Garcia and my user ID is YA.
CALL  get_user_details(YA) ×2                                      -> Error
USER  It's just letters H XDUBJ no dots.
CALL  get_reservation_details(HXDUBJ)  -> OK
      (then every turn repeats: get_reservation_details + 2× search_direct_flight)
CALL  update_reservation_flights(HXDUBJ, HAT072 / …)  -> OK
CALL  update_reservation_flights(HXDUBJ, HAT072 / …)  -> OK   (the same write, repeated next turn)
      update_reservation_baggages never called            -> DB mismatch
```

tau2's action check:
- **matched:** `get_reservation_details`, the outbound `search_direct_flight`, `update_reservation_flights`;
- **not matched:** the return search (05-21), and `update_reservation_baggages`.

### 7.7 Task 46: gained at 800 ms. Reservation code heard in one piece, no errors

- sim `8941b8f9-3ab4-48f8-93f7-dbf53db0fd86`, session `sess_2514a17db3e6447490cd`
- No write expected. Result: reward 1, `user_stop`.
- At 500 ms this task had 41 ASR segments, 24 cut-off turns and 9 errors, and hit the limit.
  At 800 ms: 12 segments, 7 cut-off turns, 0 errors.

```
USER  Confirmation code is H eight Q zero five L .
CALL  get_reservation_details(H8Q05L)   -> OK  (first try)
USER  … my user ID is SOPHIA underscore SILVA underscore seven five five seven .
      (the backend needs no user lookup: it has the reservation and keeps to the policy)
```

- This is where the longer silence helps: a code spoken in one breath now arrives as one turn.

## 8. Harness notes for this run

- **Keepalive override (I0):** `ping_interval=None` on tau2's Realtime client. 0 disconnects in both runs.
- **Hallucination re-runs:** 10 attempts were discarded, stored in `…/hallucination_discarded/`:
  - task 4 ×3, task 6 ×2;
  - tasks 15, 20, 33, 36 and 49 ×1 each.

  Only the final attempts are scored. This is why the run took 6 h 16 min.
- **Report check C1 FAIL is a matching artifact.** Agent session `sess_e6342a33fd1d416b8a68` is the
  second of four attempts at task 4, discarded by tau2. The metrics script only links discarded
  attempts that are within 30 s of the kept one. The session is correctly left out of all metrics.
- **Wall-clock pace:** 5–8 min per task. Long tasks (task 33: 33 min real time) are dominated by
  tau2's synchronous user-simulator LLM and TTS calls.

## 9. Per-task comparison (all 50 tasks)

Column meanings:
- **Text:** passes out of 4 trials in the text-2-text runs.
- **500 ms / 800 ms:** result and ending (user = `user_stop`, agent = `agent_stop`, **10-err** = tau2's error limit).
- **Cause:** the main cause of a failed task (§5.1).
- **Paired columns** are "500 ms / 800 ms" and come from each task's final agent session:
  - **ASR heard ID:** the correct user ID appears in the joined ASR transcript.
  - **FE passed ID:** it appears, in any case, in a frontend query.
  - **Authenticated:** a `get_user_details` call with the exact ID succeeded.
  - **Cut-off turns:** turns cancelled because the user kept talking.

| Task | Needs write | Text paired | Text BO | 500 ms | 800 ms | Change | Cause 500 ms | Cause 800 ms | ASR heard ID | FE passed ID | Authenticated | Tool errors | ASR segments | Cut-off turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | - | 4/4 | 4/4 | **pass** (user) | fail (**10-err**) | lost |  | frag/FE | - / Y | - / - | - / - | 5 / 9 | 29 / 21 | 19 / 12 |
| 1 | - | 2/4 | 2/4 | **pass** (user) | fail (**10-err**) | lost |  | ASR | - / - | - / - | - / - | 3 / 9 | 18 / 33 | 11 / 15 |
| 2 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | - / Y | - / Y | - / - | 6 / 7 | 59 / 25 | 35 / 7 |
| 3 | - | 3/4 | 4/4 | fail (user) | fail (user) |  | frag/FE | agent | Y / - | - / - | - / Y | 9 / 1 | 42 / 6 | 29 / 3 |
| 4 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | Y / Y | Y / Y | - / - | 9 / 8 | 27 / 18 | 15 / 5 |
| 5 | - | 4/4 | 4/4 | **pass** (user) | fail (**10-err**) | lost |  | agent | - / Y | - / Y | - / Y | 0 / 9 | 10 / 30 | 5 / 10 |
| 6 | - | 4/4 | 4/4 | **pass** (agent) | **pass** (user) |  |  |  | Y / - | Y / - | - / Y | 0 / 4 | 17 / 70 | 9 / 30 |
| 7 | Y | 1/4 | 1/4 | fail (user) | fail (user) |  | agent | frag/FE | Y / Y | - / - | Y / - | 6 / 4 | 22 / 21 | 11 / 10 |
| 8 | Y | 3/4 | 3/4 | fail (user) | fail (user) |  | frag/FE | case | Y / Y | - / Y | - / - | 5 / 1 | 38 / 5 | 24 / 3 |
| 9 | - | 4/4 | 4/4 | fail (**10-err**) | **pass** (agent) | gained | case |  | Y / Y | Y / - | - / - | 9 / 3 | 32 / 32 | 21 / 18 |
| 10 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | Y / Y | - / Y | Y / - | 1 / 5 | 27 / 18 | 9 / 7 |
| 11 | Y | 3/4 | 3/4 | fail (user) | fail (user) |  | agent | agent | Y / Y | Y / - | Y / Y | 4 / 3 | 51 / 13 | 27 / 6 |
| 12 | Y | 3/4 | 3/4 | fail (user) | fail (**10-err**) |  | agent | agent | Y / - | - / - | Y / Y | 2 / 9 | 18 / 3 | 6 / 0 |
| 13 | Y | 4/4 | 4/4 | fail (**10-err**) | **pass** (agent) | gained | frag/FE |  | Y / Y | - / - | - / Y | 9 / 5 | 19 / 14 | 11 / 5 |
| 14 | Y | 2/4 | 2/4 | fail (user) | fail (user) |  | ASR | agent | - / Y | - / Y | - / Y | 2 / 6 | 17 / 18 | 8 / 2 |
| 15 | Y | 3/4 | 3/4 | fail (user) | fail (user) |  | ASR | frag/FE | - / Y | - / - | - / - | 8 / 8 | 22 / 10 | 12 / 2 |
| 16 | Y | 3/4 | 4/4 | fail (user) | fail (user) |  | ASR | frag/FE | - / Y | - / Y | - / - | 2 / 5 | 8 / 9 | 4 / 2 |
| 17 | Y | 2/4 | 4/4 | fail (user) | fail (user) |  | ASR | ASR | - / - | - / - | - / - | 5 / 3 | 19 / 17 | 12 / 8 |
| 18 | Y | 2/4 | 4/4 | fail (**10-err**) | fail (user) |  | case | case | Y / Y | Y / Y | - / - | 9 / 5 | 40 / 30 | 20 / 17 |
| 19 | Y | 4/4 | 4/4 | fail (user) | fail (user) |  | case | case | Y / Y | Y / Y | - / - | 5 / 8 | 18 / 16 | 12 / 5 |
| 20 | Y | 2/4 | 2/4 | fail (user) | fail (user) |  | no ID | case | - / Y | - / Y | - / - | 0 / 6 | 22 / 14 | 5 / 5 |
| 21 | Y | 2/4 | 4/4 | fail (user) | fail (**10-err**) |  | ASR | ASR | - / - | - / - | - / - | 6 / 9 | 27 / 12 | 15 / 2 |
| 22 | Y | 3/4 | 4/4 | fail (user) | fail (user) |  | ASR | case | - / Y | - / Y | - / - | 3 / 4 | 15 / 7 | 9 / 2 |
| 23 | Y | 0/4 | 1/4 | fail (user) | fail (user) |  | ASR | case | - / Y | - / Y | - / - | 3 / 7 | 33 / 13 | 18 / 3 |
| 24 | Y | 3/4 | 3/4 | fail (**10-err**) | fail (**10-err**) |  | case | case | Y / Y | Y / Y | - / - | 9 / 9 | 19 / 7 | 11 / 1 |
| 25 | Y | 3/4 | 4/4 | fail (**10-err**) | fail (user) |  | ASR | case | - / Y | - / Y | - / - | 9 / 6 | 22 / 15 | 11 / 6 |
| 26 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | - / Y | - / - | - / - | 8 / 7 | 43 / 12 | 26 / 3 |
| 27 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | - / - | - / - | - / - | 3 / 3 | 45 / 28 | 31 / 7 |
| 28 | - | 4/4 | 4/4 | **pass** (agent) | **pass** (agent) |  |  |  | Y / - | Y / - | Y / - | 0 / 0 | 18 / 7 | 9 / 3 |
| 29 | Y | 0/4 | 0/4 | fail (**10-err**) | fail (user) |  | ASR | agent | - / Y | - / Y | - / Y | 9 / 4 | 23 / 33 | 18 / 14 |
| 30 | Y | 3/4 | 2/4 | **pass** (user) | fail (user) | lost |  | case | Y / Y | Y / Y | Y / - | 2 / 3 | 39 / 9 | 19 / 4 |
| 31 | - | 3/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | Y / Y | - / - | Y / Y | 6 / 4 | 76 / 54 | 41 / 18 |
| 32 | Y | 0/4 | 1/4 | fail (user) | fail (user) |  | case | case | Y / Y | Y / Y | - / - | 7 / 5 | 35 / 9 | 19 / 3 |
| 33 | Y | 1/4 | 0/4 | **pass** (user) | fail (user) | lost |  | agent | Y / - | Y / - | Y / Y | 4 / 7 | 54 / 19 | 23 / 4 |
| 34 | - | 4/4 | 4/4 | fail (**10-err**) | **pass** (user) | gained | case |  | Y / - | Y / - | - / Y | 9 / 0 | 24 / 26 | 13 / 10 |
| 35 | Y | 2/4 | 2/4 | fail (user) | fail (user) |  | frag/FE | case | Y / Y | Y / Y | - / - | 5 / 5 | 16 / 7 | 7 / 1 |
| 36 | - | 4/4 | 4/4 | **pass** (agent) | **pass** (user) |  |  |  | - / - | - / - | - / - | 0 / 0 | 7 / 20 | 2 / 6 |
| 37 | Y | 0/4 | 2/4 | fail (**10-err**) | fail (user) |  | ASR | ASR | - / - | - / - | - / - | 9 / 3 | 36 / 21 | 16 / 12 |
| 38 | - | 4/4 | 4/4 | fail (**10-err**) | **pass** (user) | gained | frag/FE |  | Y / Y | Y / Y | - / - | 9 / 8 | 25 / 16 | 10 / 6 |
| 39 | Y | 1/4 | 1/4 | fail (user) | fail (user) |  | ASR | frag/FE | - / Y | - / Y | - / - | 5 / 6 | 47 / 22 | 26 / 10 |
| 40 | Y | 1/4 | 3/4 | fail (**10-err**) | fail (user) |  | case | no ID | Y / Y | Y / - | - / - | 9 / 0 | 19 / 13 | 12 / 4 |
| 41 | - | 4/4 | 4/4 | **pass** (agent) | **pass** (user) |  |  |  | - / Y | - / - | Y / - | 4 / 6 | 23 / 19 | 6 / 6 |
| 42 | Y | 3/4 | 4/4 | fail (**10-err**) | fail (user) |  | case | case | - / Y | Y / Y | - / - | 9 / 6 | 22 / 12 | 12 / 6 |
| 43 | - | 4/4 | 4/4 | **pass** (user) | fail (**10-err**) | lost |  | case | - / Y | - / Y | - / - | 1 / 9 | 16 / 34 | 14 / 12 |
| 44 | Y | 0/4 | 0/4 | fail (**10-err**) | fail (user) |  | ASR | ASR | - / - | - / - | - / - | 9 / 5 | 15 / 17 | 10 / 5 |
| 45 | - | 4/4 | 4/4 | fail (**10-err**) | **pass** (agent) | gained | case |  | Y / Y | Y / Y | - / - | 9 / 0 | 21 / 3 | 13 / 0 |
| 46 | - | 4/4 | 4/4 | fail (**10-err**) | **pass** (user) | gained | case |  | Y / Y | - / Y | - / - | 9 / 0 | 41 / 12 | 24 / 7 |
| 47 | - | 4/4 | 4/4 | fail (**10-err**) | fail (**10-err**) |  | ASR | case | - / Y | - / Y | - / - | 9 / 9 | 26 / 23 | 15 / 8 |
| 48 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | Y / Y | Y / Y | - / - | 9 / 8 | 20 / 9 | 12 / 2 |
| 49 | - | 4/4 | 4/4 | **pass** (user) | **pass** (user) |  |  |  | Y / Y | Y / Y | Y / - | 4 / 0 | 52 / 3 | 24 / 1 |

Session totals in this table differ slightly from §3 (779 / 354 cut-off turns): the report also
counts the discarded hallucination attempts.

## 10. Recommendations

Most failures are at login, so the login fixes (§10.1) come first. After login, the main problem is
the stateless backend (§10.2). All fixes are inside the agent and keep the benchmark unchanged.
§13 has the evidence behind each one.

### 10.1 User-ID login

The table shows what each fix would recover in the 800 ms run, counted per task and added
cumulatively. "Failed tasks reached" counts the tasks among the 32 failures that would then have the
correct ID. Getting the ID right doesn't guarantee a pass: only 4 of the 11 tasks that logged in
passed (§4).

| # | Fix | Where | Problem it removes | Tasks gaining a correct ID | Logged in (of 50) | Failed tasks reached (of 32) |
|---|---|---|---|---|---|---|
| – | Today | – | – | – | 11 | 7 (they failed after login) |
| 1 | **Lower-case the ID** | backend tool adapter | right characters, wrong case: `Mia_Kim_4397`, `MIA_KIM_4397` | +19 | 30 | +14 |
| 2 | **Canonicalise the ID** | backend tool adapter | stray dots, spaces or underscores: `EM_MA_KIM_9957`, `A_A_R_A_V_G_A_R_C_I_A_1177`, `DAIKIMULLER1116` | +5 | 35 | +4 |
| 3 | **Assemble spelled IDs from the raw ASR** | agent pipeline, before the frontend; raw transcript to the backend | an ID split over several turns, or dropped or garbled by the frontend although the ASR had it | +7 | 42 | +2 |
| 4 | **Reconcile spelling with the spoken name** | backend prompt or adapter | letters misheard during spelling while the name said as a word was right | +2 | 44 | +1 |
| 5 | **ASR for telephone audio** | ASR model or config | the ASR never produced the ID (s/f, b/d/v, m/n, p/t/e confusions on 8 kHz audio) | the last 6 | up to 50 | +4 |

What to implement:

1. **Lower-case and canonicalise the ID before `get_user_details`** (fixes 1 and 2). Do it in code, in
   the backend's tool adapter, not only in a prompt:
   - map "underscore" and number words, and join spelled letters;
   - remove dots, spaces and filler words;
   - lower-case the result;
   - **check it against `^[a-z]+_[a-z]+_\d{4}$` before calling the tool.** If it doesn't match (`YA`,
     `eth`, `699`, `_7340`, `Ah_ah_ah_two_…`), ask the user for the missing part instead of calling.
     Every failed lookup counts toward tau2's 10-error limit, which ended 8 tasks at 800 ms and 15 at
     500 ms.
2. **Assemble spelled IDs from the ASR stream** (fix 3):
   - while an ID is incomplete (it ends in "underscore", or has no 4-digit tail), join consecutive ASR
     results instead of treating each one as a new request;
   - pass the raw user transcript to the backend together with the frontend's query;
   - keep every complete ID the user has said as a candidate. Change the frontend prompt's "the latest
     user turn overrides older values" so that a partial or worse re-spelling can't replace a complete
     earlier ID (task 26: `Amelia_Sanchez` became `Amelia_Fancy`).
3. **Stop the retry loop.** Never send an ID that has already failed (54 such calls at 800 ms, 87 at
   500 ms). After a failure:
   - read the ID back in lower case ("m, i, a, underscore, k, i, m, underscore, four, three, nine,
     seven, is that right?");
   - ask only about the doubtful part, not for the whole spelling again. Spelled name parts are no
     more accurate than names said as words (55% against 57%, §13.2).
4. **Reconcile spelling with the spoken name** (fix 4). When spelled letters conflict with the name
   said as a word, and the conflict is a commonly confused pair (s/f, b/d/v, m/n, p/t/e), prefer the
   spoken name. Alternatively, confirm that one letter phonetically ("S as in Sierra?"). Use general
   knowledge of name spellings, not the benchmark's `audio_difficulty.json`.
5. **Improve the ASR on 8 kHz telephone audio** (fix 5):
   - a telephony-trained or 8 kHz fine-tuned model;
   - letter and digit context, or word boosting, while an ID is being collected;
   - measure letter accuracy on μ-law 8 kHz audio specifically.
6. **Harness:** render the simulated user's vocal tics so they don't look like letters. "ahem… hkh" is
   transcribed as "H K K" and ended up in 2 lookups. Alternatively, filter it in the agent.

There is **no tau2 tool that returns the exact user ID** (§13.3). `get_reservation_details` returns
the reservation's `user_id`, but the policy requires the user to provide their user ID, and
reservation codes suffer the same ASR errors. At most it can support a read-back confirmation, and
whether a grader accepts that is uncertain.

### 10.2 After login

7. **Give the backend the conversation history, or at least the raw transcript of recent turns.**
   At 800 ms, 7 of the 11 tasks that logged in failed afterwards. The backend:
   - repeated writes (task 33 ran `update_reservation_flights` twice);
   - forgot parts of a request (task 33 never made the baggage change);
   - lost a reservation code it had already found (task 5).

### 10.3 Turn detection and evaluation

8. **Keep `silence_duration_ms: 800` with `honor_client_values: false`.** It halves cut-off turns
   (779 → 354) and misheard IDs (13 → 5 tasks), with no latency cost. Try 1000 ms in one run, to see
   whether the 68 remaining fragment calls drop.
9. **Run more than one trial per setting** to measure differences of 2–3 tasks. The 6-gained /
   6-lost swap in §6 shows how much a single trial varies.

## 11. Paths

| What | Where |
|---|---|
| This campaign (800 ms) | `$NEW/` (`README.md` run card) |
| This run's tau2 results, traces, audio | `$NEW/fba_voice_paired_airline_regular_sil800/tau2/fba_voice_paired_airline_regular_sil800/` (live copy: `$TAU2/data/simulations/fba_voice_paired_airline_regular_sil800/`) |
| This run's agent sessions (60) | `$NEW/fba_voice_paired_airline_regular_sil800/agent/events.jsonl` |
| This run's agent config (with the 800 ms edit) | `$NEW/fba_voice_paired_airline_regular_sil800/agent/config/tau3_eval.yaml`, diff in `…/provenance/agent_uncommitted.diff` |
| §7.2 report (800 ms) | `$NEW/_reports/fba_voice_airline_regular_sil800/fba_voice_report.md` |
| §7.1 metrics (800 ms) | `$NEW/_reports/fba_voice_paired_airline_regular_sil800/{agent_metrics,interaction_metrics}.json` |
| 500 ms campaign | `$OLD/` (`README.md`, `observations/2026-09-24_fba-voice-paired-airline-regular.md`) |
| 500 ms report | `$OLD/_reports/fba_voice_airline_regular/fba_voice_report.md` (also in `$NEW/_reports/`) |
| Text-2-text baselines | `$TAU2/data/simulations/fba_paired_airline_base_4trials/`, `$TAU2/data/simulations/fba_backend_only_airline_base_4trials/` |
| Failure attribution output (800 ms) | `$NEW/observations/failure_attribution.txt` |
| Per-task comparison data | `$NEW/observations/compare_runs.json` |
| Scripts | `$NEW/observations/attribute_failures.py`, `$NEW/observations/compare_runs.py`; sources in `$TAU2/misc/prototypes/observations/` |

## 12. How the numbers were computed

- **Pass^1, terminations and interaction metrics:** tau2 `compute_metrics` and `tau2 submit
  interaction-metrics` (runbook §7.1).
- **Agent-side latency, tokens and turn outcomes:** `fba_voice_metrics.py` (runbook §7.2).
- **Failure causes (§5):** `attribute_failures.py [RUN]`, run from `$TAU2`.
  - It joins each task's final agent session (matched by simulation start/end time) to its
    `backend_tool_calls` and `tool_output_in` events.
  - It labels each failed call in this order: case only; retry of an identical failed call;
    incomplete ID; frontend garbled an ID the ASR had heard; ASR misheard.
  - A failed task's main cause:
    1. logged in at some point → agent errors;
    2. otherwise, any case-only failure → case;
    3. otherwise, the correct ID in the joined ASR transcript → fragments/frontend;
    4. otherwise → ASR.
- **Funnel and per-task table (§4, §9):** `compare_runs.py`, which uses the same session join.
  - The ASR transcript is lower-cased, with number words and "underscore" mapped to characters.
  - "FE passed ID" ignores case, spaces and underscores.
  - "Needs write" means the task's expected actions include anything other than lookups, searches
    and `calculate`.

## 13. Why user IDs come out wrong, and what would fix them (added 2026-09-26)

### 13.1 How many failures each fix would recover (per task)

Per lookup call, only 40% of non-lower-case IDs (80 of 200) have the right characters. That share
understates the fix: a task retries many wrong IDs, and one correct lookup is enough. Per task, the
800 ms run breaks down as follows (the 500 ms run is shown for comparison):

| What the task's lookups and ASR contained, in order of the cheapest fix | 800 ms | 500 ms | Fix |
|---|---|---|---|
| Logged in | 11 | 10 | – |
| 1. A lookup had the right characters in the wrong case | **19** | 13 | lower-case the ID |
| 2. A lookup had the right characters with extra dots, spaces or separators (`EM_MA_KIM_9957`, `A_A_R_A_V_G_A_R_C_I_A_1177`, `DAIKIMULLER1116`) | 5 | 3 | canonicalise the ID |
| 3. The ASR had the full ID across 1–4 consecutive utterances, but no lookup used it | 7 | 4 | assemble the ID from the raw transcript |
| 4. The ASR heard the name as words and the digits, but the spelling was wrong | 2 | 4 | reconcile the spelling with the spoken name |
| 5. The ASR never produced the ID | 6 | 16 | ASR / acoustics |

Fixes 1–3 are deterministic text processing inside the agent. At 800 ms they would give a correct ID
in 31 more tasks: 42 of 50, against 11 now. A correct ID doesn't guarantee a pass (§4: only 4 of the
11 tasks that logged in passed), and a few of these tasks need no login at all. Tasks per category at
800 ms:
- 1: 2, 4, 8, 10, 18, 19, 20, 22, 23, 24, 25, 26, 30, 32, 35, 38, 42, 43, 47
- 2: 0, 7, 16, 39, 48
- 3: 9, 15, 40, 41, 45, 46, 49
- 4: 28, 44
- 5: 1, 17, 21, 27, 36, 37

### 13.2 Root causes of the wrong-character IDs

The 800 ms run has 120 lookup calls with wrong characters (133 unique wrong IDs, counting the
case-only ones). Where each error came in:
- **The ASR never had the correct ID before the call:** 40 unique IDs with wrong characters, 14
  incomplete, 11 with noise words.
- **The ASR had it, and the frontend or backend lost it:** 33 unique IDs with wrong characters, plus
  formatting and fragment cases.

**Dropped letters are the biggest error class, not misheard letters.** Across the 134 near-miss IDs
in both runs, 228 letters were dropped and 35 added, against 55 substitutions. Dropped letters come
from fragments: part of the ID sits in another utterance, or the frontend or backend truncates it.

**Letter substitutions** (expected → heard):
- s→f 9, d→b 6, n→m 4, v↔d/b 6, g→b/d 2, p→e/t 2, and a few others.
- These are the classic telephone confusions. tau2 sends 8 kHz μ-law audio (`telephony_enabled: true`
  in `tasks_voice.json`), which removes the energy above 4 kHz that separates /s/ from /f/.
- The "E-set" letters (B, D, E, G, P, T, V, Z) differ only in a short burst at the start.
- Examples: `FILVA`/`FOPHIA` for Silva/Sophia, `BAVIS` for Davis, `DARCIA` for Garcia, `OMNR` for Omar.

**How the frontend loses IDs** (nemotron-3.5-lightning, reasoning off):
- It copies the ASR's word splits: `MOHAMED_Sil_va_9265`, `Ame_Lia_Davis_8890`, `ANYA_G_ARCIA_5901`.
- It turns spelled letters into underscores: `A_A_R_A_V_G_A_R_C_I_A_1177`.
- It drops letters from a correct transcript. "A a rav underscore a h M E D underscore six six nine
  nine" became `ARAV_MED_6699` (task 9).
- It keeps a stale value from history. `11177` from an earlier mishearing survived a correct re-spelling
  of `1177` (task 15).
- It follows the prompt rule "the latest user turn overrides older values" and replaces a correct
  earlier ID with a worse re-spelling. "Amelia underscore Sanchez" became `Amelia_Fancy_4739` after
  the agent asked for the spelling (task 26).
- It puts non-ID speech into the ID: `Ah_ah_ah_two_Whole_thing_R_A_J_underscore_S_A_N` and
  `A_J_underscore_We_update_and_see_Now` (task 1).
- It reads vocal-tic sound-words as letters: `Sophia_Martin_A_H_K_K` and `RA_AHMED_H.K`. The I0
  harness renders a cough as "ahem… hkh", and the ASR transcribes it as "H K K". This affected 8 ASR
  results and 2 lookups; it is a harness artifact, not a user behaviour.

**How the backend adds to it** (nemotron-3-ultra):
- It calls `get_user_details` with whatever string the frontend sent, without checking the format
  `^[a-z]+_[a-z]+_\d{4}$`. `YA`, `eth`, `699` and `_7340` were all sent as user IDs.
- It repeats identical failed IDs (54 calls).
- Every failed lookup counts toward tau2's 10-error limit.

**Spelling letter by letter isn't more accurate than saying the name.** In the ASR, a name part said
as a word was correct 57% of the time (102 cases), and a name part spelled out 55% (109 cases). The
500 ms run gave 45% and 47%. So repeatedly asking the user to spell it out doesn't help. Both tau2's
own agent instructions and ours ask for it, and tau2's user guidelines also tell the simulated user to
spell (`data/tau2/user_simulator/simulation_guidelines_voice.md`).

### 13.3 Is there a tau2 tool that returns the exact ID?

**No.** The airline tools are:
- `book_reservation`, `calculate`, `cancel_reservation`;
- `get_reservation_details`, `get_user_details`;
- `list_all_airports`, `search_direct_flight`, `search_onestop_flight`;
- `send_certificate`, `transfer_to_human_agents`;
- `update_reservation_baggages`, `update_reservation_flights`, `update_reservation_passengers`;
- `get_flight_status`.

(`src/tau2/domains/airline/tools.py`.)
- There is no search by name, email or phone. `get_user_details` needs the exact ID.
- **`get_reservation_details(reservation_id)` returns the reservation's `user_id`.** Task 30 found
  `james_taylor_7043` this way. But it can't replace user-ID authentication:
  - The policy says: "The agent must first obtain the user id from the user" (booking), and "The user
    must provide their user id" (modify and cancel).
  - Reservation codes are spelled too, with the same ASR errors ("HGLT3" for `EHGLP3`; "H9Z1C" for
    `H9ZU1C`).
  - It can still be used to **confirm** a candidate: read the returned ID back and have the user say
    yes, so that the user has provided it. Whether a grader accepts that is a policy question; it is
    not a guaranteed pass.
- τ³ voice measures exactly this skill. `data/tau2/domains/airline/audio_difficulty.json` gives the
  authentication method as "user_id (spoken as 'firstname underscore lastname underscore numbers')",
  with per-name difficulty and homophones (Mia/Mya, Li/Lee, Sophia/Sofia, Mohamed/Muhammad).
- Adding a lookup tool would change the benchmark, and the results would no longer be comparable.

### 13.4 Fixes, in order (all agent-side and within the benchmark rules)

1. **Canonicalise the ID before any lookup** (fixes 1 and 2 in §13.1; 24 tasks at 800 ms). Do it in
   the backend tool adapter:
   - lower-case it;
   - map "underscore", number words and spelled letters;
   - drop dots, spaces and filler words;
   - if the result doesn't match `^[a-z]+_[a-z]+_\d{4}$`, **ask the user instead of calling the tool**.
     This also saves tau2 error-limit budget.
2. **Assemble spelled IDs from the raw ASR stream** (fix 3; 7 tasks):
   - join consecutive ASR results while an ID is incomplete (it ends in "underscore", or has no 4-digit tail);
   - pass the raw transcript to the backend;
   - keep every complete candidate the user has said; don't let "latest wording wins" drop an earlier
     complete ID.
3. **Never retry an ID that has already failed.** Read the ID back ("m, i, a, underscore, k, i, m,
   four three nine seven, is that right?") and ask only about the doubtful part.
4. **Reconcile spelling with the spoken name** (fix 4). When the letters conflict with the name said
   as words, and the conflict is in a known confusable pair (s/f, b/d/v, m/n, p/t/e), prefer the name.
   Or ask for that one letter with a phonetic word ("S as in Sierra?"). Use general knowledge for
   homophones, not the benchmark's annotation file.
5. **ASR** (the remaining 6 tasks, and most first-attempt errors):
   - a telephone-band (8 kHz) fine-tuned or telephony model;
   - letter/digit context or word boosting during ID collection;
   - evaluate letter accuracy on 8 kHz μ-law audio specifically.
6. **Harness note:** render vocal tics so they aren't letter-like ("hkh" → a non-letter sound or real
   audio), or filter "H K K" after "ahem" in the agent. This is small (2 lookups).

Scripts behind §13: `id_error_analysis/` next to this document (`an.py` extracts every lookup with its
ASR and frontend context; `cls.py` classifies them; `fix.py` gives §13.1; `spell.py` compares spelled
and spoken names).
