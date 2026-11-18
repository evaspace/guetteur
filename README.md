# guetteur

Finds exam and test dates in Moodle and school-planning iCal feeds, and in a Microsoft 365 school mailbox.
Prints them, or adds the new ones to a JSON file. Python standard library only.

```text
$ guetteur --ics "$MOODLE_URL" --ecues ecues.json
moodle.epita.fr: 2 exams or tests since 2026-09-01.
2026-11-16  Test à distance  MATHS APIS : TEST 1 FR 16 novembre  [maths/S1/APIS]
2026-11-24  Test à distance  QCM2  [physique/S1/Mécanique]
```

Exam dates at a school are scattered: the Moodle calendar has the online tests (one event when a test opens,
one when it closes), the planning has the exam weeks, and the rest only comes by mail (« les examens auront
lieu la semaine du 26/10 »). guetteur reads the three, keeps what looks like an exam, merges duplicates and
tags each date with its course.

Written for [EPITA Doomscrolling](https://epitadoomscrolling.pierref.eu), an exam training site for EPITA prépa
students, whose exam calendar it refreshes twice a day. By [Morgan Pierrefeu](https://morgan.pierref.eu).

## Install

```bash
pip install git+https://github.com/evaspace/guetteur
```

Or copy `guetteur.py` (and `epita_mail.py` for the mailbox). Python 3.9 or newer, no dependency.

## Use

```bash
guetteur --ics URL                       # print exams found in a feed
guetteur --ics URL1 --ics URL2 --mail    # several feeds plus the mailbox
guetteur --ics URL --merge exams.json    # add only the new dates to exams.json
guetteur --ics URL --json --since 2027-01-01
```

iCal links hold a secret token. Pass them in `GUETTEUR_ICS` (space-separated) rather than on the command line,
so they stay out of `ps` and shell history. guetteur never prints them: a source is named by its host.

Moodle link: Calendrier > Exporter le calendrier > « Tous les cours », « Événements récents et à venir » >
Obtenir l'URL du calendrier.

Each found date is:

```json
{"debut": "2026-12-14", "fin": "2026-12-18", "titre": "Analyse : primitives, intégrales et suites",
 "type": "Examen", "source": "moodle.epita.fr", "ecue": "maths/S1/APIS"}
```

`fin` is there only for a date range. `type` is `Examen` or `Test à distance`.

### Course tags

`--ecues` takes a JSON file mapping your course ids to their titles, plus short codes found in Moodle course
names (see [`examples/ecues.json`](examples/ecues.json)):

```json
{"ecues": {"maths/S1/APIS": "Analyse : primitives, intégrales et suites"},
 "aliases": {"meca": "physique/S1/Mécanique"}}
```

A date gets the course whose title words all appear in the event, or whose id words do (« MATHS APIS »),
else the first alias found. Without the file, no tag.

### Mailbox

Microsoft 365 refuses IMAP with a password. `epita-mail` signs in with OAuth2 using Thunderbird's public client
id, so no app has to be registered at the school. Once, from any terminal (the browser can be on another
machine):

```bash
epita-mail login    # open the link, sign in, paste back the final https://localhost/?code=... address
epita-mail check    # number of mails in INBOX
```

The refresh token is saved in `~/.config/epita-mail/token.json` (mode 600) and renewed on each run. It expires
after 90 days without use. The mailbox is opened read-only; only subjects with exam words are fetched. Mails
announcing grades are skipped.

### Cron

```cron
5 7,19 * * *  GUETTEUR_ICS='https://...' guetteur --mail --ecues ~/ecues.json --merge ~/exams.json >> ~/.local/state/guetteur.log 2>&1
```

## How it works

- A short RFC 5545 reader (unfolding, escapes, UTC to Paris time, exclusive all-day end).
- An event counts when its title, description or category has an exam word (examen, partiel, QCM, test,
  contrôle, rattrapage…). Moodle « s'ouvre » / « se termine » pairs become one date.
- Mails: the « Ouverture le lundi 5 octobre 2026, 07:00 : … » subject, else the first date in the subject or in
  a sentence with an exam word (« 5 octobre », « 26/10 », « semaine du 26/10 » = Monday to Friday). Without a
  year, the closest one after the mail.
- Duplicates (feed, announcement, opening mail, FR and EN copies) are dropped: same start day and title, or same
  start day and type.

## Limits

- Mail parsing reads French announcements as EPITA sends them. Other wording can be missed or misread: check the
  output before trusting `--merge`.
- Days are in Europe/Paris.

## Tests

```bash
python3 -m unittest tests/test_guetteur.py
```

## License

MIT
