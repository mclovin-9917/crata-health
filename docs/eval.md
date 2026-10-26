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

Every setting sets all the model settings itself, so `backend/.env` cannot change a run. With the backups off, a call that hangs fails the visit, and it counts as a miss.

What it counts: allergy clashes caught, with the right colour (red or amber) and naming the medicine at fault; false alarms where an allergy is recorded and nothing should be flagged (A6, A7), and separately where none is recorded; billed-but-not-prescribed lines caught (named by brand or ingredient) and false alarms; medicines read (by brand or active ingredient) and medicines that are not on the prescription; quotes the code rejected; seconds per visit; tokens per model, and the cost on Token Factory (NVIDIA build is free). A visit that was not read counts as a miss in every rate, never as left out.

Not measured: recordings (no audio cases; transcription with and without drug-name hints was not run), and a quantity check (dropped from the app on 2026-10-17).

```
backend\.venv\Scripts\python scripts\eval\make_cases.py
backend\.venv\Scripts\python scripts\eval\run_eval.py --config main --runs 3
backend\.venv\Scripts\python scripts\eval\run_eval.py --config ultra
backend\.venv\Scripts\python scripts\eval\run_eval.py --config omni
backend\.venv\Scripts\python scripts\eval\run_eval.py --config gemma
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

What this does and does not show: on clean, printed prescriptions and bills of the kind the app was built for, the whole pipeline found every planted problem and raised no false alarm. It does not show how the app does on handwriting, poor photos, recordings, or medicines outside its brand table; see "How new they are" above.
