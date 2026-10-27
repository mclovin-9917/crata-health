# Testing guide

A walk through every feature with made-up people and documents. Everything here is synthetic: the names, phone numbers, clinics and medicines are invented, and no SMS is ever sent (the app shows the code on screen).

Tick the boxes as you go. When something looks wrong, note the step number and what you saw.

## 0. Before you start

- Start the app with `start-app.bat` (it builds the screens and opens http://127.0.0.1:8000).
- The demo people (section 1) start again from scratch every time the app starts, so whatever you did as them is gone after a restart. People you register with a new phone number stay.
- The documents to upload are in `samples\`. Make them again any time with:

```
backend\.venv\Scripts\python scripts\make_demo_docs.py
```

- For the phone tests (camera, microphone, QR scanner), run `share-tailscale.bat` and open the HTTPS address it prints on your phone.

## 1. The people

Demo accounts (pick them from the list on the Sign in screen):

| Who | Role | What they have |
|---|---|---|
| Asha Verma | Patient | A record with a sulfa allergy (hives), hypertension, Amlodipine, an appendectomy, a dengue ER visit; a blood pressure check-up from 14 Jul 2026, confirmed; and the planted prescription and bill visit waiting in To review (section 6). Ravi is her trusted person. |
| Ravi Verma | Patient, Asha's trusted person | His own empty record; sees Asha under "People who trust me". |
| Dr. A. Mehta | Doctor | No record; scans patients' QR codes. |
| Meera Iyer | Patient, phone 90000 10001 | Year of birth 1985, blood group O+. An empty record: sections 3 to 5 fill it. |
| Kabir Iyer | Patient, phone 90000 10002 | Year of birth 1982. Meera invites him as her trusted person in section 10. |
| Rohan Das | Patient, phone 90000 10004 | Year of birth 1990, blood group A+. Used to check privacy. |
| Dr. Neha Rao | Doctor, phone 90000 10003 | Registry number SYN-2002. |

Meera, Kabir, Rohan and Dr. Neha Rao also have their phone numbers, so "Sign in with your phone" with their number reaches the same person (unless that number was registered before they were added; then the number opens the older account).

## 2. Sign in and sign up

- [ ] 2.1 Sign in as Asha from the demo list. Home shows her name and how many visits are in her record. Sign out (top right).
- [ ] 2.2 Sign in with 90000 10001. Type a wrong code first: it says the code is wrong. Then the right one. It goes straight to Meera's Home (she has an account already) with an empty record.
- [ ] 2.3 Sign out and sign in with a new number, 90000 10005. It asks for a profile: register Tara Sen as a patient (year of birth 1995). Home opens with an empty record.
- [ ] 2.4 Sign out and sign in with 90000 10005 again: no sign-up step this time, it goes straight to Tara's Home.
- [ ] 2.5 Sign in as Dr. Neha Rao from the demo list. Her Home shows "Scan a QR code".
- [ ] 2.6 Register a doctor with a new number (90000 10006, Doctor, SYN-2003): the registry number is asked for, and the doctor Home opens.
- [ ] 2.7 Type a number like 12345: it says Indian mobile numbers start with 6, 7, 8 or 9.

## 3. Meera's first visit: typed notes

Signed in as Meera. Add a visit, no files, and type this in the notes box:

> I am allergic to penicillin, I got a rash and swelling. I have a thyroid problem and take Thyronorm 50 every morning, I have been on it for five years.

- [ ] 3.1 The reading screen lists the steps and finishes in under a minute.
- [ ] 3.2 Review has three boxes to review, each with one row: Allergies noted (Penicillin, rash and swelling), Diagnosis (a thyroid condition), Medicines (Thyronorm 50, levothyroxine). Each says "Suggested" and has Confirm, Don't add and "Details and edit".
- [ ] 3.3 In a box, "Details and edit", then "See where this came from": it names your typed notes, the words it came from are shown, highlighted in the notes, and the row says it was said by the patient.
- [ ] 3.4 Edit Thyronorm (for example change the dose to "50 mcg"), save: it shows "Edited by you" and still needs Confirm.
- [ ] 3.5 Confirm all three (one tap in each box). "All reviewed" appears. Home now shows the allergy and the medicine.
- [ ] 3.6 Something to notice: a medicine the patient only mentions ("I use an inhaler when needed") becomes a note, not a medicine. Only a doctor or a document can add a new prescription; a patient's words count for a medicine only when they already take it long-term.

## 4. Meera's second visit: prescription and bill (the checks)

Add a visit with two photos: `samples\test-meera\visit2-prescription.png` and `visit2-bill.png` (or the single `visit2.pdf`, which holds both pages).

What Review should show at the top:

- [ ] 4.1 Amber "Possible link to an allergy": Cefixime is a cephalosporin, related to penicillins.
- [ ] 4.2 Amber "On the bill, not prescribed": the bill has plain Montair (montelukast) while the prescription has Montair LC (montelukast + levocetirizine), worded as a different mix.
- [ ] 4.3 Amber "On the bill, not prescribed": Ondem 4 (ondansetron). The brand table doesn't know Ondem, so the app looks it up on 1mg (one Tavily search); if the lookup fails, the AI's own guess is used and the flag says "Matched by AI".
- [ ] 4.4 No flag for Thyronorm on the bill: it is a refill of the medicine she confirmed in visit 1. No flag for Dolo or Cefixime, which were prescribed. No flag for the cotton roll.
- [ ] 4.5 "See where this came from" on a flag shows the prescription line and the bill line, labelled "From the bill" for the bill.
- [ ] 4.6 The boxes below come from the prescription only: Diagnosis (Acute pharyngitis), Allergies noted (Penicillin, "Already in your record"), Medicines (Cefixime, Montair LC, Dolo 650). Nothing comes from the bill. Sore throat, fever, the advice and the follow-up are under "Added from this visit": already in your record, no tap needed.
- [ ] 4.6b In the Medicines box, Cefixime and Montair LC are shown open under "These 2 need a closer look" (each is in a warning above); the box button says "Confirm the other 1" and only covers Dolo 650. (If a warning names the Penicillin suggestion from this visit, that one is open too.)
- [ ] 4.7 The visit is dated 14 Oct 2026 (printed on the documents).
- [ ] 4.8 Tap "Don't add" on Montair LC: the Montair flag changes to "You chose not to add Montair LC from this visit". Tap "Add to record": it goes back.
- [ ] 4.9 Confirm the rest (the box buttons, then Cefixime and Montair LC on their own).
- [ ] 4.10 Add a visit with only typed notes: "Doctor prescribed Tab Taxim-O 200 twice a day for 5 days." Review shows "Contains cefixime (from 1mg)", and "1mg" opens the Taxim-O 200 page.

## 5. Meera's third visit: a bill on its own

Add a visit with only `samples\test-meera\visit3-bill-only.png`.

- [ ] 5.1 A note says the visit is a pharmacy bill only, and a bill can include medicines bought for someone else.
- [ ] 5.2 One Medicines box: Calpol 500 (paracetamol), Amoxicillin 500, ORS Sachet. Gloves are not suggested. ORS shows no ingredients (the AI did not name one in the live run).
- [ ] 5.3 Red "Possible allergy clash": Amoxicillin is a penicillin, and her record lists a penicillin allergy. Amoxicillin is shown open under "This one needs a closer look", and the box button says "Confirm the other 2".
- [ ] 5.4 Tap "Don't add" on Amoxicillin (say it was for someone else): the red flag disappears.
- [ ] 5.5 Confirm Calpol and ORS (one tap on the box).

## 6. Asha's visit: the planted demo case

Sign in as Asha. The visit is already read: open it from Home or My record ("To review"). To see it read live, Add a visit with `samples\demo-asha\prescription.png` and `bill.png` (on the hosted demo, "No documents to hand? Download sample files" on Add a visit gives the same files).

- [ ] 6.1 Red "Possible allergy clash": Septran DS contains sulfamethoxazole, and her record lists an allergy to sulfa drugs (hives).
- [ ] 6.2 Amber "On the bill, not prescribed": Pantocid 40 (pantoprazole).
- [ ] 6.3 The sulfa allergy suggestion from the prescription shows "Already in your record".
- [ ] 6.4 Press "Try a careful read" (at the foot of Review, under "How this visit was read", behind "More info"; only on a visit you uploaded yourself, in that browser: the ready-made visit has no files in your browser). It takes about two minutes per photo; suggestions you had not reviewed, and facts added as read that you had not corrected, are replaced; confirmed and corrected ones stay.

## 6a. The lighter Review

On Asha's visit (or the one you uploaded from her documents). Only allergies, medicines, diagnoses and events wait for a tap.

- [ ] 6a.1 To review has boxes: Allergies noted, Diagnosis, Medicines. Each lists its rows in short form with "Confirm all", "Don't add any" and "Go one by one" (a box with one row says "Confirm", "Don't add", "Details and edit").
- [ ] 6a.2 In Medicines, Septran DS is not in the short list: it is open under "This one needs a closer look", with the line "Part of the allergy clash above" (the red card at the top says the warning in full, once). The box button says "Confirm the other 3". In Allergies noted, the sulfa allergy is open the same way: the red warning rests on it, so it is decided on its own and one tap cannot turn it down and clear the warning.
- [ ] 6a.3 "Go one by one" opens the row cards (Confirm, Edit, Don't add, where it came from) for the other three; confirming one of them leaves two in the box.
- [ ] 6a.4 "Confirm the other 2": they move to In your record. Septran DS stays open until you decide on it.
- [ ] 6a.5 On the Diagnosis box, "Don't add": the diagnosis goes under Not added with "Add to record".
- [ ] 6a.6 "Added from this visit" lists what you reported (fever, cough), the advice and the follow-up. Each row is one line, with no Confirm button. Its chevron ("Show details") opens "Read from the visit", Edit and Remove, and "See where this came from" sits under the row. Edit one: it says "Edited by you". Remove one: it is struck through with "Add it after all".
- [ ] 6a.7 Home's latest visit already lists what was added as read (the symptoms and the follow-up), even before the boxes are done. Its chip still says "to review" until the boxes are. While something waits, Home also shows an amber banner under the top, "N suggestions to review" with a "Review suggestions" button, and the Latest visit card then has only a quiet "Open this visit" link.

## 6b. An emergency or hospital stay

As Meera, Add a visit, then "An emergency or hospital stay".

- [ ] 6b.1 Choose "Admitted to hospital", hospital "City Hospital", what happened "Pneumonia", started today, "I am still there" ticked. Log it.
- [ ] 6b.2 The stay's page shows it with "Still there". It is already in the record (your own entry, marked from memory, no document); nothing to confirm.
- [ ] 6b.3 Emergency card: "In hospital now: Admitted at City Hospital since (today), Added by the patient" at the top.
- [ ] 6b.4 Back on the stay, tap "I went home", keep today, Save. The card no longer shows it; the timeline still lists "Pneumonia, admission".
- [ ] 6b.5 Edit the stay (change what happened): the record entry shows "Edited by you".
- [ ] 6b.6 My record, Visits: the stay is a row in its month with "Hospital admission" or "Emergency visit" as its name, its place and date, and "Still there" while open. Tap the row: its page shows the dates and "Added by you".

Then the documents from a stay. Log a second stay: "Admitted to hospital", City Hospital, "Pneumonia", started on **10 Oct 2026**, still there. On its page, "Add documents", choose `samples\test-meera\stay-discharge-summary.png` and `stay-hospital-bill.png`, "Add documents".

- [ ] 6b.7 Waiting for your OK: Community acquired pneumonia (Diagnosis) and the home medicines (Azithral, Dolo 650, Ascoril LS). No suggestion for the admission itself (your stay already is that). Added as read, under "Added from this visit": the chest X-ray and the blood count as tests, with the chest X-ray finding and the leucocyte count (14200 per cu mm, dated 13 Oct 2026) under them, and the follow-up.
- [ ] 6b.8 Ceftriaxone, Pantoprazole and Duolin, given in hospital, are notes, not medicines. They are listed once under a "Given in hospital 3" heading inside Advice and notes in "Added from this visit" (the words "Given in hospital:" are not repeated on every line). The same grouping shows in My record: Summary under Notes, and on the stay's page (tap its row under Visits).
- [ ] 6b.9 Amber "Possible link to an allergy": the bill's Ceftriaxone is a cephalosporin, related to her penicillin allergy. No "on the bill, not prescribed" flags: a hospital bill lists what was used there.
- [ ] 6b.10 The stay card says your documents show you went home on 13 Oct 2026, with the words it came from. "Use this date" closes the stay.
- [ ] 6b.11 The home medicines show their course from the day you went home: Azithral "3 days, 13 Oct 2026 to 15 Oct 2026", not from 10 Oct. (In My record, "From 10 Oct" next to a fact is the stay it came from, not the start of the medicine.)

## 6c. Test values and "Add report"

Numbers printed on a report, or written on a prescription, go into your record as they are printed, without a confirm tap. Code checks each one against the document before it is kept. Two ways to try it.

**A report read as part of a visit.** Add a visit, choose `samples\test-meera\visit4-prescription-tests.png` and `visit4-lab-report.png`, "Add visit".

- [ ] 6c.1 Review shows an "Added from this visit" card, already in your record, with no Confirm button; under Tests, the values sit under their test. Under CBC: Hemoglobin 13.2 g/dL (normal 13 to 17), Total leucocyte count 14200 /cu mm (normal 4000 to 11000, the comma in "14,200" read right), Platelet count 250. HbA1c 6.9 % and TSH 2.8 sit under their own tests.
- [ ] 6c.2 "Urine sugar: Nil" is a value in words. Numbers written on the prescription (BP 138/88 as two values, Weight 62 kg, Pulse 78) are under "Other results".
- [ ] 6c.3 The tests themselves (CBC, HbA1c, TSH) are read as they are too, with no confirm tap. Open a value's chevron ("Show details"): it says "Read from the document". Its "See where this came from" shows the printed row highlighted.
- [ ] 6c.4 In a value's "Show details", Edit it (say Hemoglobin to 12.9) and save: the row says "Edited by you". Remove one: it shows struck through with "Add back".
- [ ] 6c.5 Reject the CBC test: its untouched values go with it; confirming it again ("Add to record") brings them back. A value you edited or removed yourself is left alone.
- [ ] 6c.6 The emergency card shows none of these values.
- [ ] 6c.7 My record, Summary, "Tests and reports": one row per test and report (CBC, HbA1c, TSH) with its date (the report's, 17 Oct 2026, not the visit's), how many values it has, and an amber "Outside printed range" chip when a value is outside the printed range. An "Open My health numbers" link opens My health numbers. If the same test came in two visits, both reports are listed under its name. The visit's row under Visits counts the values ("3 facts, 6 results"); tap it and its page lists them under the visit's tests, dated by the report.

**A report added later on a test.** Add a visit with only `visit4-prescription-tests.png`; its tests are already in the record.
- [ ] 6c.8 CBC, HbA1c and TSH each show "Add report" on this visit's Review (under Tests in "Added from this visit"), and in My record: on the visit's page (tap its row under Visits), and in Summary under "Tests and reports" (a report with no values yet shows "Add report" instead of opening). If you uploaded the same prescription twice, Summary lists each visit's report under the test's name.
- [ ] 6c.9 On CBC, "Add report", choose `visit4-lab-report.png`, "Add report". Back on the visit, the values appear under their tests (all three tests, since one report holds them all), the visit's own date has not moved, and no other suggestion was added.
- [ ] 6c.10 My record, Summary, "Tests and reports": tap the CBC row. The report view shows a table: Measure, Result, Unit, Printed range, and in "Compared with range" an amber "Above range" or "Below range" (with its icon) on a value outside the printed range; nothing on the others. It says "Report of 17 Oct 2026" and links to its visit only when the visit's day is different. A value you edited says "Edited by you". On a narrow phone the rows stack with no sideways scrolling. "Back to Home" at the top takes you back.

## 7. Recording a consult (phone over HTTPS, or the laptop)

- [ ] 7.1 Add a visit, tap Start recording, read a short made-up consult aloud (a complaint, a diagnosis, a medicine with its dose and days, and some advice). The "doctor agreed to being recorded" tick is needed before it uploads.
- [ ] 7.2 Review waits for your OK on the penicillin allergy and azithromycin 500 mg once a day for 3 days, and adds paracetamol 650 if needed (as a note) and the follow-up as read; amoxicillin is not suggested (the doctor said not to give it). Quotes are marked "From the recording" with a time.

## 8. My record

- [ ] 8.1 Summary shows each fact once (for Meera: penicillin allergy, the thyroid condition, Thyronorm, Cefixime...), in this order: Allergies, Diagnoses, Medicines, then the Timeline, then "Tests and reports". Symptoms, Follow-ups and Notes are under "From recent visits". Each fact's chevron ("Show details") shows who said it and where it was seen ("Seen in 2 visits" when it is more than one).
- [ ] 8.2 Visits groups the visits by month ("October 2026"). Each is one short row: the visit's name (or "Prescription", "Documents" and so on when it has none; a small "Suggested" marker when the AI wrote the name), the date and the hospital or clinic when one is printed, and a count ("3 facts, 6 results") or a "6 to review" chip. Stays are rows too, with their kind as the name and a green rule at the left. Tap a row to open the visit.
- [ ] 8.3 Tap a medicine name (for example Cefixime): a summary from trusted sites opens, with the site names and a "not advice for you" note. The first lookup can take about 10 seconds.
- [ ] 8.4 Add an event by hand with "Add a past event" on the timeline card (for example "Admission, 2023, pneumonia"): it appears in the timeline marked from memory, no document.
- [ ] 8.5 Open a medicine's details (the chevron) and tap "I stopped taking this": it leaves the emergency card.
- [ ] 8.6 Delete a visit: open it from Visits and tap the quiet "Delete this visit" link at the bottom of its page (a visit that could not be read has the same link). A panel says how many facts and test values go, any suggestions still waiting, and which facts only this visit says. What would leave your emergency card is named in an amber line ("These leave your emergency card"); it is worked out from the card itself, so it can include a medicine another visit also mentions when this visit holds its latest course. If an older visit's statement would come back onto the card, a plain line says so.
- [ ] 8.7 "Keep visit" closes the panel and changes nothing. "Delete visit" erases the visit: it is gone from Visits, its facts are gone from Summary and (if they were only there) from the emergency card, and its Review page says not found. A fact that another visit also says stays.
- [ ] 8.8 Delete a hospital stay the same way (use one that is still open, "Still there"; log a new one if you closed both): the panel says "this stay" and the amber line names it as "Hospital admission: ..." and "Shown as In hospital now"; afterwards the stay is gone from Visits and the emergency card.
- [ ] 8.9 Keyboard: when the panel opens, the focus is on "Keep visit" (the safe choice), so Enter alone never deletes.
- [ ] 8.10 Edit a visit's name: open a visit and tap "Edit name" next to the heading (or "Add a name" when it has none). Type a name (up to 60 characters) and Save: the heading changes and the "Suggested" marker goes, because the name is now yours. Check the Visits list shows it, and that reading the visit again carefully keeps your name. "change date" works the same way and shows where the date came from (printed on the document, upload day or date you set). Cancel and Save put the focus back on the button you used.
- [ ] 8.11 The Home hero, and the top of My record, show age (from the birth year), sex and blood group when they are known, and nothing when none is.
- [ ] 8.12 Add something to a visit: open a visit that was read and tap "Add something to this visit" at the bottom. The focus moves to the first field. Choose Allergy, type "Penicillin" and Save: the row shows in the visit with a "You added" chip, and the allergy is on your emergency card. Try Medicine with a dose and days, then Edit and Remove the new row. Leaving the name empty, or typing 0 or 2.5 days, shows a message under the field and saves nothing.

## 8b. My health numbers

Sign in as Asha first (she has readings and two lab reports already), then as Meera.

- [ ] 8b.1 Home has a "My health numbers" card under My record. It opens the screen.
- [ ] 8b.2 Asha's table, one row per measure, newest first: Blood pressure (134 / 84 mmHg, 4 readings, a small trend with two lines), Weight (72.8 kg, 3 readings), HbA1c (6.1 %, 2 readings), Total cholesterol (214 mg/dL), LDL cholesterol (136), HDL cholesterol (43), Triglycerides (172), Blood sugar (fasting) (108), Creatinine (0.9). Each row has the printed range, the date of the latest reading and the number of readings. On a phone (390 px) there is no sideways scroll: the date sits under the name, and the range under the value.
- [ ] 8b.3 An amber chip saying "Above" on HbA1c, total and LDL cholesterol, triglycerides and fasting sugar (each with the warning icon). No chip on HDL (43 is inside 40 to 60) or creatinine. No chip on blood pressure or weight: no report prints a range for them, so their range column shows a dash.
- [ ] 8b.4 Click the HbA1c row (or Tab to its name and press Enter): the row opens under itself with a bigger chart. The chart has a value on every dot, the two dots outside the range are amber, and the pale band carries no text. Under the chart one line says "Shaded band: 4 to 5.6, printed on your 14 Sep 2026 report". Then the readings newest first: 14 Sep 6.1 and 15 Jun 6.4, each with "From the report" that opens that lab visit. Click the row again to close it; opening another row closes this one.
- [ ] 8b.5 Nothing on this screen says what a number means or what to do: only the amber chip, the amber "Above/Below the printed range ..." note on a reading with "Ask your doctor what it means for you", and the note at the bottom.
- [ ] 8b.6 Open Blood pressure: two lines with values on the dots, and two "shaded" lines only if both lines have a printed range. Its readings say "Added by you" (Asha entered them). Edit one (say 134 to 130): it says "edited by you" and the chart moves. Remove one: it is struck through, leaves the chart and the count, and "Add back" brings it back. If only one of the two lines has a reading from the latest day, the Latest cell names each line ("Systolic 134 ... (date)") instead of "134 / 84".
- [ ] 8b.7 Add a reading as Asha: "Add a reading", Blood pressure, 128 and 82, today. The blood pressure card shows 128 and 82 and 5 readings. Weight, with a different unit typed (say "lb" instead of "kg"), splits it into two cards, "Weight, kg" and "Weight, lb", each with its own scale: nothing is converted. A reading added with the unit left blank gets no unit (the app does not guess one) and, having no unit, is never compared with a range.
- [ ] 8b.8 As Meera (see section 6c for her lab report): Add a reading, "Something else", name "HbA1c", unit "%", 5.2. It lands on the same HbA1c card as her report's 6.9, marked "you added". A reading of 6.2 is marked amber against the range on her report ("Above the range on your report of 17 Oct 2026 (4 to 5.6)"); 5.2 is not.
- [ ] 8b.9 A reading of something no report has (say weight) gets no marker at all.
- [ ] 8b.10 The emergency card shows none of these numbers. My record > Visits does not list the readings under "Added by you" (they live here); My record > Summary has an "Open My health numbers" link that links here.

## 8c. Before a visit and Ask about my health

Sign in as Asha. Each "Ask" that is not answered from the cache uses one Tavily search; keep this section to a few questions.

- [ ] 8c.1 Home has two tiles, "Before a visit" and "Ask about my health".
- [ ] 8c.2 Before a visit: type "Itchy red rash on both arms, worse at night. Mild fever yesterday.", since "4 days", "Make my summary". Within about 30 s: "Who to see" marked Suggested (a skin specialist or a general physician) with one line why and "A suggestion, not a diagnosis"; the summary lists both symptoms, your words in full, 4 days, the sulfa allergy in red, her medicines, conditions and test values outside their printed range, and up to 3 questions. Nothing tells you to start or stop a medicine.
- [ ] 8c.3 Print: only the summary card prints (no header, no buttons). Back on Before a visit, the summary is listed; Delete asks first, then removes it.
- [ ] 8c.4 Type "Sudden chest pain and I can't breathe properly": the red "Get emergency care now" card comes first with both warning signs and "Call 112 or go to the nearest emergency department". "No chest pain, just a cough" does not show it.
- [ ] 8c.5 Ask "Is salt bad for high blood pressure?": a few points, each with the site's name (NHS, Mayo Clinic, MedlinePlus, WHO, CDC) and "See the source" opening the exact words and the link; "Searched with your condition: Hypertension"; the grey note "General information ... not advice for you". Ask it again: the same answer, faster (no new search).
- [ ] 8c.6 Ask "Can I eat banana with chest pain and breathlessness?": only the red emergency card, no answer. Ask "Who won the cricket match?": a polite note that it answers health questions only.
- [ ] 8c.7 Sign out and sign in as Ravi: Ask shows none of Asha's questions.
- [ ] 8c.8 Speak instead (on Before a visit and on Ask): allow the microphone, say a sentence, Stop. Within a few seconds the words appear in the box and can be edited; nothing is sent until you press the main button. Leave the screen while recording: the browser's mic indicator turns off.
- [ ] 8c.9 Under an answer, "Ask a follow-up": "What about pickles?" after the salt question answers about pickles and blood pressure. Points sit under "Good to know", "Be careful" or "With your conditions"; each [n] opens the exact words, and "Sources" lists the sites by number.
- [ ] 8c.10 The summary for the doctor reads as rows: symptoms, since, allergies (red chips), medicines and conditions (chips), recent events, tests out of range (name, value, High or Low, printed range, date), questions; "In my words" is folded but prints.

## 9. Emergency card and doctors

- [ ] 9.1 As Meera, open Emergency card: the card (allergies, current medicines, conditions), then the QR code with one sentence about it, then the timeline. The card link and a note about the address are behind "More info".
- [ ] 9.2 Sign out (or use a second browser) and sign in as Dr. Neha Rao. Scan a QR code: point the phone camera at Meera's QR on another screen, or paste the card link. The card opens.
- [ ] 9.3 Open the card link while signed out: it shows the form first, asking for a doctor's name and registry number ("no account"), with "Have a doctor account? Sign in" as a link under the button. Fill in any synthetic name and SYN-0003. After the card, "Close the card" clears it from the screen (a signed-in doctor gets "Back to Home"). On a phone, use a QR or link made from the Tailscale address: one made at 127.0.0.1 only opens on this computer, and "More info" on the Emergency card says so.
- [ ] 9.4 As Meera, open "Who opened my emergency card" on Home (under Safety and sharing). The Access log says "No changes found in this log" ("More info" explains how), and lists both openings, the second marked "no account", with alerts.

## 10. Trusted people

Inviting (Meera and Kabir):

- [ ] 10a.1 As Meera, Home, "My trusted people" (under Safety and sharing). Invite 90000 10002. It shows under "Waiting for an answer" with the date it lapses.
- [ ] 10a.2 Try inviting 90000 10002 again (refused: already invited), and Meera's own number (refused).
- [ ] 10a.3 Sign out. Sign in as Kabir Iyer (demo list, or 90000 10002). His Home shows "Meera Iyer asked you to be their trusted person". Accept.
- [ ] 10a.4 Kabir's Home now shows "People who trust me: Meera Iyer". In it, Emergency gives him only her name, blood group and allergies, plus a QR code for a doctor.
- [ ] 10a.5 As Meera: Kabir is listed under trusted people, and an alert says he accepted.
- [ ] 10a.6 As Kabir, "Stop being their trusted person" (a quiet link, then confirm). Meera gets an alert. Invite him again and this time Decline as Kabir: Meera gets an alert that the invitation was declined (the number is masked).
- [ ] 10a.7 As Meera, invite him once more, accept as Kabir, then Remove him from Meera's list (a quiet link, then confirm): Kabir gets an alert and can no longer start an emergency for her.

Starting an emergency (Asha and Ravi, already linked):

- [ ] 10.1 Sign in as Ravi. "People who trust me" shows Asha. Start an emergency: he sees only her name, blood group and allergies, plus a QR code for a doctor that works once, for 30 minutes.
- [ ] 10.2 As Dr. Mehta, open that code: the card opens, and the log says it used Ravi's code. Opening the same code again is refused.
- [ ] 10.3 As Asha, the access log and alerts show both events.

## 11. Privacy

- [ ] 11.1 As Rohan, open a link to one of Meera's visits (copy the address from Meera's Review screen): it says not found. 
- [ ] 11.2 As Rohan, Meera never appears anywhere.
- [ ] 11.3 Under the hood is only for admins. Make Dr. Neha Rao an admin, sign in again, and open it from Home: models, timings, the list of people, but no one's record.

```
backend\.venv\Scripts\python scripts\set_admin.py --phone 90000 10003
```

## 12. Phone and browsers

- [ ] 12.1 Repeat a few steps on your phone over the Tailscale address: Take a photo of a sample document on screen, Review, Emergency card, QR scan.
- [ ] 12.2 Try Chrome and one other browser (Edge, Firefox or Safari).
