# Crata Health

A health record that belongs to the patient. You add a doctor's visit by recording the consult (once the doctor agrees) or photographing the prescription, bill and reports. NVIDIA Nemotron models read them and suggest what goes into your record, each fact with the exact words it came from. You confirm what matters. The app checks a new medicine against your allergies and the bill against the prescription, and keeps an emergency card that a doctor can open from a QR code, with every opening logged and you alerted. Before a visit, it turns what you feel into a summary for the doctor; between visits, it answers questions about your record and your diet.

Built for the Nebius x NVIDIA Global AI Hackathon. Everything in this repo is synthetic: made-up people, documents and visits.

**Live demo:** https://crata.onrender.com (pick a demo account on the sign-in page; Asha Verma has a full record and a visit waiting for review). The free server sleeps when idle, so the first page can take about a minute.

## What it does

- **Add a visit:** a recording of the consult, photos, screenshots or PDFs of prescriptions, bills and reports, or typed notes. Your browser keeps your files until you have reviewed the visit; the server reads them in memory and never stores them.
- **Suggestions with proof:** every suggested fact keeps the exact words it came from ("See where this came from"), and code checks those words are really in what was read or heard. Allergies, medicines, diagnoses and events enter your record only when you confirm them, one box per kind. Everything else (what you reported, tests, advice, follow-ups, test values) goes in as read, marked, and you can change or remove it.
- **Checks:** a medicine that clashes with an allergy in your record (red for the same ingredient or family, amber for a related family), and a medicine on the bill that was not prescribed (amber). The wording says what to ask a doctor or pharmacist, never what to take.
- **Test reports and My health numbers:** values from lab reports, filed under their test, and charted over time with readings you add yourself. A number is marked only when it is outside the range printed on your own report.
- **Hospital stays:** log an emergency visit or admission while you are there, and add the discharge summary later.
- **Emergency card:** allergies, current medicines, conditions and recent events behind a QR code. A doctor who opens it is logged, and you and your trusted people are alerted.
- **Trusted people:** invited by phone number. In an emergency they get a one-time code for a doctor, never your record.
- **Medicine summaries:** tap a medicine to read what trusted health sites say about it, each point quoted.
- **Before a visit:** type or say how you feel. The app asks up to 3 quick questions (tap an answer, type one, or skip), then saves a summary you can print or show the doctor: which kind of doctor to see and why, your symptoms in your own words, your answers, and your allergies, medicines, conditions, recent events and out-of-range test values, taken from your confirmed record by code. Warning signs such as chest pain or trouble breathing are caught by code first and lead to "Get emergency care now".
- **Ask about my health:** questions about your own record ("What tablets am I taking for BP?") are answered from your confirmed record, with no web search. General questions about food and daily life ("Is papaya good for me?") are answered with short points from trusted health sites, each quoted, grouped, with numbered sources, and your allergies and conditions taken into account. One follow-up question keeps the one before it.
- **Speak instead:** on both screens you can say it instead of typing. The recording is written down and never stored.

## Where we use NVIDIA Nemotron

- **Nemotron 3 Nano Omni** (NVIDIA build) reads document photos and scanned PDF pages, transcribes consult recordings in pieces of about 30 seconds, and writes down what you say when you tap "Speak instead".
- **Nemotron 3 Super** does the reasoning: it sorts documents (prescription, bill, report) and reads bills line by line, extracts structured entries each with a quote, matches brand names to active ingredients, names drug families for the allergy check, translates Hindi left in details, and writes the medicine summaries. For Before a visit and Ask it asks the quick questions, suggests the kind of doctor, picks the search words and the record items, and writes the quoted points. Extraction runs at temperature 0.
- **Nemotron 3 Ultra** was compared with Super in the evaluation: the same answers on every case, at about 4 times the cost per visit, so the app uses Super.

Policy stays in code, not in the model: which facts need your confirmation, dates, who said what, the checks themselves, the warning signs, and the list of 15 kinds of doctor the model may choose from.

## Where we use Nebius Token Factory

Every model call goes through one OpenAI-compatible client with a provider per step (`backend/app/ai/client.py`, `backend/app/config.py`).

- **Nemotron 3 Super on Token Factory** does all the reasoning for the hosted demo and the evaluation.
- **Gemma 3 27B on Token Factory** reads document photos when NVIDIA build is slow or busy. Nano Omni is not served on Token Factory, so reading stays on NVIDIA build first.
- Each visit records which model answered each step, and the evaluation counts tokens and cost per model.

## Where we use Tavily

Every search goes through one client (`backend/app/ai/search.py`) and is limited to trusted health sites.

- **Brand names** our table does not know: Tavily finds what the medicine contains, so the allergy check still works.
- **Medicine summaries:** short points about a medicine, each quoted from its source.
- **Ask about my health:** the model picks a few general words from the question (a food, a condition), and code checks them before they go out: at most 8 words, no digits, no part of your name, never your record. Every point in the answer must quote its source, and code checks the quote is there. Results are cached for 30 days, and each account may ask 20 questions a day.

## Evaluation

14 held-out synthetic cases, each a made-up patient's record plus a prescription and a pharmacy bill photo, run through the whole app 3 times. Nothing was tuned on them. Method, cases and limits: [docs/eval.md](docs/eval.md).

| What | Result (42 visits) |
|---|---|
| Allergy clash caught, right colour, right medicine | 15 of 15 |
| False alarms | 0 |
| Medicine billed but not prescribed, caught | 12 of 12 |
| Medicines read from the prescription | 75 of 75, none invented |
| Time per visit | median 26.5 seconds |
| Token Factory cost | $0.16 for all 42 visits |

These are clean, printed documents of the kind the app was built for. They do not show how it does on handwriting, poor photos or recordings.

Comparisons on the same cases: Nemotron 3 Ultra gave the same answers as Super at about 4 times the cost. Nano Omni was right on every visit it read, but NVIDIA build's free tier turned away 4 of 14; Gemma 3 27B on Token Factory read all 14 right, which is why it is the backup. Full tables in [docs/eval.md](docs/eval.md).

## Run it yourself

You need Python 3.11+, Node 22+, and API keys for NVIDIA build (reading documents and audio), Nebius Token Factory (reasoning) and Tavily (medicine look-ups and Ask about my health).

Backend, in Windows PowerShell:

```
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env
.venv\Scripts\python -m pytest
```

Put your keys in `backend/.env` (never commit it). On macOS or Linux, use `.venv/bin/python` and `cp`.

Frontend, in a second terminal (on Windows use `npm.cmd`; the default script policy blocks `npm.ps1`):

```
cd frontend
npm.cmd install
npm.cmd run build
```

Then start the server and open http://127.0.0.1:8000 (the backend serves the built app; the API is under `/api`, with docs at `/docs`):

```
cd backend
.venv\Scripts\python -m uvicorn app.main:create_app --factory
```

On Windows, `start-app.bat` does all of this in one go once `backend\.venv` exists. With Docker:

```
docker build -t crata .
docker run --rm -p 8000:10000 -e PORT=10000 --env-file backend/.env crata
```

The demo people are built again from scratch on every start. To try every feature with them and the sample documents in `samples/`, follow [docs/testing-guide.md](docs/testing-guide.md). Start with a fresh database: one made by another version of the app may not load.

The evaluation: `python scripts/eval/make_cases.py`, then `python scripts/eval/run_eval.py --config main --runs 3` (from the repo root, with the backend's Python).

## Limits

- **The quote check** proves the quoted words are in what the model read or heard. It cannot prove the model read the paper or heard the audio correctly, or that the words support the label. That is why you confirm.
- **Facts read as they are** skip the confirm tap. A misheard symptom or a garbled advice line stays until you notice it; a doubtful one carries an amber note.
- **Test values** are checked by code: the number must be printed in the row the model quoted, next to the measure's name. A misread blurry number is still what gets checked and kept.
- **Checks are decision support** to talk over with a doctor or pharmacist. An allergy added later does not flag older visits.
- **Hindi audio** is written in Latin letters, and Hindi number words can be misheard. This goes for "Speak instead" too.
- **Before a visit and Ask** give general information, not a diagnosis or advice for you. The kind of doctor is a suggestion. The warning signs are a fixed list in English and common Hinglish, so a sign worded another way can be missed. The evaluation above does not cover these screens.
- **The AI summary** in Ask is at most 2 sentences, written only from record items picked by code or from points that passed the quote check, and dropped if it tells you to start, stop or skip a medicine. It can still word something less carefully than the points it came from.
- **Prototype shortcuts:** sign-in codes are shown on screen (no SMS provider), demo accounts for judges, doctor registry numbers are not verified, alerts are in-app only, and text read from documents is stored unencrypted (fine for synthetic data only).

## License

The code is licensed under the GNU Affero General Public License v3.0 ([LICENSE](LICENSE)). If you run a modified version as a service, the AGPL asks you to share your source code with its users.

The name "Crata Health" and its logo are not covered by this license.
