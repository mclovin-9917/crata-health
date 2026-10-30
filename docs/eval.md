# Eval

How well Crata reads a visit's documents and catches the two problems it checks for, measured on cases it was never tuned on.

## The cases

14 synthetic cases (`scripts/eval/cases.py`). Each is one made-up patient, with an allergy or a current medicine in their record or nothing, who adds one visit: a prescription and a pharmacy bill, drawn as phone photos (slightly turned and blurred, printed type, not handwriting; `scripts/eval/make_cases.py`, `samples/eval/<case>/`). No prompt was tuned on them.

How new they are: the documents are new, the situations less so. Some mirror what the app was built and tried on: a cephalosporin with a penicillin allergy, sulfamethoxazole with a sulfa allergy, a stomach medicine on the bill only, and Augmentin and Montair LC reused. Ten of the 13 brand names are in the app's hand-written table of brands (written on 2026-10-12, before these cases); only Mox, Taxim-O and Telma have to be worked out through the web search or the model. Read the numbers as "the app on clean, printed documents of the kind it was built for", not as a test of new drugs or handwriting.

| Group | Case | In the record | Prescription | On the bill too | Should find |
|---|---|---|---|---|---|
| Allergy | A1 | Penicillin | Mox 500 (amoxicillin), Dolo 650 | | Clash, red |
| | A2 | Penicillin | Augmentin 625 (amoxicillin), Pan 40 | | Clash, red |
| | A3 | Sulfa drugs | Bactrim DS (sulfamethoxazole), Dolo 650 | | Clash, red |
| | A4 | Ibuprofen | Brufen 400 (ibuprofen), Pan 40 | | Clash, red |
| | A5 | Penicillin | Taxim-O 200 (cefixime), Dolo 650 | | Clash, amber (related family) |
| | A6 | Penicillin | Azithral 500 (azithromycin), Dolo 650 | | Nothing |
| | A7 | Sulfa drugs | Dolo 650, Allegra 120 | | Nothing |
| | A8 | Nothing | Mox 500, Pan 40 | | Nothing |
| Billed but not prescribed | B1 | Nothing | Azithral 500, Dolo 650 | Pan 40 | Pan 40 |
| | B2 | Nothing | Augmentin 625, Montair LC | Allegra 120 | Allegra 120 |
| | B3 | Nothing | Glycomet 500 (metformin) | Telma 40 | Telma 40 |
| | B4 | Nothing | Dolo 650 | Brufen 400 | Brufen 400 |
| | B5 | Nothing | Mox 500, Dolo 650 | | Nothing |
| | B6 | Amlong 5 (amlodipine), current | Azithral 500 | Amlong 5 | Nothing (already taken) |

## How it runs

`scripts/eval/run_eval.py` runs every case through the real app in memory, the way a patient's upload runs: the perceive model reads the photos, Nemotron 3 Super extracts the facts with their quotes, and code does the checks. It scores each run in plain code against the case's answers and saves everything it saw in `scripts/eval/results/`.

Settings:
- **main**: as deployed for judging. Nano Omni on NVIDIA build reads, Super on Token Factory reasons, with the backups on (Gemma 3 27B on Token Factory reads when NVIDIA build does not answer in time, Super on NVIDIA build reasons when Token Factory does not). How often a backup answered is counted per step. 3 runs.
- **ultra**: Nemotron 3 Ultra on Token Factory reasons instead of Super. Backups off. 1 run.
- **omni** and **gemma**: Nano Omni alone against Gemma 3 27B alone, for reading. Backups off. 1 run each.
- **nano**: Nemotron 3 Nano 30B on Token Factory reasons instead of Super. Backups off.
- **--photos** (with any setting): the pages are read as phone photos made in memory by `photos.py`.

Every setting sets all the model settings itself, so `backend/.env` cannot change a run. With the backups off, a call that hangs fails the visit, and it counts as a miss.

What it counts: allergy clashes caught, with the right colour (red or amber) and naming the medicine at fault; false alarms where an allergy is recorded and nothing should be flagged (A6, A7), and separately where none is recorded; billed-but-not-prescribed lines caught (named by brand or ingredient) and false alarms; medicines read (by brand or active ingredient) and medicines that are not on the prescription; quotes the code rejected; seconds per visit; tokens per model, and the cost on Token Factory (NVIDIA build is free). A visit that was not read counts as a miss in every rate, never as left out.

Not measured: recordings (no audio cases; transcription with and without drug-name hints was not run), and a quantity check (dropped from the app on 2026-10-17).

```
backend\.venv\Scripts\python scripts\eval\make_cases.py
backend\.venv\Scripts\python scripts\eval\run_eval.py --config main --runs 3
backend\.venv\Scripts\python scripts\eval\run_eval.py --config ultra
backend\.venv\Scripts\python scripts\eval\run_eval.py --config omni
backend\.venv\Scripts\python scripts\eval\run_eval.py --config gemma
backend\.venv\Scripts\python scripts\eval\run_eval.py --config main --runs 3 --photos
backend\.venv\Scripts\python scripts\eval\run_eval.py --config nano --runs 3
backend\.venv\Scripts\python scripts\eval\time_features.py --n 5
```

## Results

### Main setup, 3 runs of the 14 cases (2026-10-26)

| What | Result |
|---|---|
| Visits read | 42 of 42 (100%) |
| Allergy clash caught | 15 of 15 (100%) |
| ... with the right colour (red or amber) | 15 of 15 (100%) |
| ... naming the medicine at fault | 15 of 15 (100%) |
| False alarms, allergy recorded and nothing to flag (A6, A7) | 0 of 6 |
| False alarms, no allergy recorded | 0 of 21 |
| Billed but not prescribed caught | 12 of 12 (100%) |
| Billed false alarms | 0 across 42 visits |
| Medicines read | 75 of 75 (100%) |
| Medicines not on the prescription (invented) | 0 |
| Quotes rejected by the code check | 0 |
| Gemma backup read the photos (NVIDIA build too slow) | 16 of 42 visits (38%) |
| Seconds per visit | median 26.5, fastest 18.1, slowest 93.0 |
| Token Factory cost, all 42 visits | $0.16 (Nemotron 3 Super 118 calls, 210,174 tokens in, 111,542 out; Gemma 17 calls) |

The same answer came back in all three runs for every case. Nano Omni on NVIDIA build is free; it answered 67 calls (157,048 tokens in). Raw output: `scripts/eval/results/main-20261026-1300.json`.

### Comparisons, 1 run of the 14 cases each, backups off (2026-10-26)

| | Main (Super, Omni + Gemma backup, 3 runs) | Ultra reasons | Omni alone reads | Gemma alone reads |
|---|---|---|---|---|
| Visits read | 42 of 42 | 14 of 14 | 10 of 14 | 14 of 14 |
| Allergy clash caught (right colour and medicine) | 15 of 15 | 5 of 5 | 4 of 5 | 5 of 5 |
| False alarms | 0 | 0 | 0 | 0 |
| Billed but not prescribed caught | 12 of 12 | 4 of 4 | 1 of 4 | 4 of 4 |
| Medicines read | 75 of 75 | 25 of 25 | 18 of 25 | 25 of 25 |
| Median seconds per visit | 26.5 | 22.2 | 28.9 | 29.4 |
| Token Factory cost per visit | $0.0037 | $0.0158 | $0.0029 | $0.0041 |

- **Super vs Ultra:** the same answers on every case. Ultra cost about 4 times as much per visit ($0.22 against about $0.05 for the same 14 visits on Super), so Super stays in the app.
- **Nano Omni vs Gemma 3 27B for reading:** every visit Omni read was fully right. The 4 it missed were refused by NVIDIA build's free tier ("503 ResourceExhausted: Worker local total request limit reached"), not misread. Gemma on Token Factory read all 14 right. This is why the app keeps Gemma as Omni's backup: in the main runs it stepped in for 16 of 42 visits.

Raw output: `scripts/eval/results/ultra-20261026-1309.json`, `omni-20261026-1314.json`, `gemma-20261026-1321.json`.

### Phone photos, model sizes and feature timings (2026-10-30)

Three new runs of the 14 cases, 3 runs each (42 visits), with nothing changed in the app or the prompts.

- **Phone photos** (`--photos`): each clean page is turned, in memory, into a quick phone photo: on a desk, tilted and turned up to 4 degrees, a soft shadow across one side, darker, slightly blurred, saved as a JPEG at quality 55 to 68 (`scripts/eval/photos.py`, seeded by case and file so every run sees the same photo). Main setup.
- **Nano reasons**: Nemotron 3 Nano 30B (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) on Token Factory instead of Super, clean pages, backups off.
- **Ultra reasons**, again: the 26 Oct comparison had 1 run; this one has 3, so all three sizes are compared on the same 42 visits. Clean pages, backups off.

| | Super (main, clean, 26 Oct) | Super, phone photos | Nano reasons | Ultra reasons |
|---|---|---|---|---|
| Visits read | 42 of 42 | 42 of 42 | 41 of 42 | 42 of 42 |
| Allergy clash caught (right colour and medicine) | 15 of 15 | 15 of 15 | 14 of 15 | 15 of 15 |
| Allergy false alarms | 0 | 0 | 0 | 0 |
| Medicines read | 75 of 75 | 75 of 75 | 71 of 75 | 75 of 75 |
| Medicines invented | 0 | 0 | 0 | 0 |
| Billed but not prescribed caught | 12 of 12 | 12 of 12 | 12 of 12 | 12 of 12 |
| Billed false alarms | 0 | 0 | 5 | 9 |
| Backup answered | Gemma read 16 of 42 | never | off | off |
| Median seconds per visit | 26.5 | 24.1 | 29.2 | 14.1 |
| Token Factory cost, 42 visits | $0.157 | $0.148 | $0.074 | $0.648 |
| Cost per visit | $0.0037 | $0.0035 | $0.0018 | $0.0154 |

- **Phone photos:** the same answers as the clean pages on every count, and no backup was needed (Nano Omni read every photo). A photo of a printed page is not handwriting: handwriting is still not measured.
- **Nano:** half the cost of Super, but one visit failed (Nano returned an empty answer on the extraction, so that visit's clash was lost), it left Dolo 650 off the prescription twice (and the bill check then flagged the Dolo on the bill as not prescribed), and on A5 it flagged Taxim-O on the bill in all three runs although it had read it on the prescription.
- **Ultra:** the same clashes and medicines as Super, faster (median 14.1 s against 26.5 s), at 4.1 times the cost. All 9 false alarms have one cause: Ultra matched "Mox 500" on the bill to moxifloxacin instead of amoxicillin, so the bill line no longer matched the prescription (A1, A8, B5, every run).
- **So Super stays:** the only size with no misses and no false alarms, at under half a US cent per visit.

Raw output: `scripts/eval/results/main-photos-20261030-0602.json`, `nano-20261030-0607.json`, `ultra-20261030-0618.json`. Nano's price ($0.06 in, $0.24 out per million tokens) is Nebius's rate as listed on OpenRouter; we did not find it on a Nebius page.

**Feature timings** (`scripts/eval/time_features.py`, 5 rounds for the demo patient Asha on a fresh database, Super on Token Factory, 2026-10-30):

| Feature | Median seconds (min to max) | Token Factory cost per use |
|---|---|---|
| Before a visit: the quick questions | 1.0 (0.7 to 3.6) | $0.0006 for questions and summary together |
| Before a visit: the summary | 1.2 (1.2 to 1.7) | (above) |
| Ask, a question about the record | 1.0 (1.0 to 2.2) | $0.0002 |
| Ask, a food question (web search) | 5.1 (3.2 to 9.6) | $0.0011, plus one Tavily search |

One of the 5 food questions ("Is dates good for me?") failed with a 502. Raw output: `scripts/eval/results/features-20261030-0543.json`.

What this does and does not show: on clean, printed prescriptions and bills of the kind the app was built for, the whole pipeline found every planted problem and raised no false alarm. It does not show how the app does on handwriting, poor photos, recordings, or medicines outside its brand table; see "How new they are" above.
