#!/usr/bin/env python3
"""guetteur: find exam and test dates in iCal feeds (Moodle, school planning) and in the school mailbox.

    guetteur --ics URL [--ics URL ...] [--mail] [--ecues ecues.json] [--merge calendar.json]

iCal URLs can also come from the GUETTEUR_ICS environment variable (space-separated), which keeps these
secret links out of `ps` and shell history. --mail reads the Microsoft 365 mailbox set up with
`epita-mail login` (see epita_mail.py). Without --merge it prints what it found; with --merge it adds the
new dates to a JSON array file and prints only the new ones.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

__version__ = '0.1.0'
TZ = ZoneInfo('Europe/Paris')
EXAM = re.compile(r'\b(examen|exam|eval|partiel|midterm|final|test|contr[oô]le|qcm|rattrapage)', re.I)
STOP = {'et', 'de', 'des', 'du', 'la', 'le', 'les', 'l', 'd', 'a', 'en', 's1', 's2'}


def words(text):
    t = unicodedata.normalize('NFKD', text.lower()).encode('ascii', 'ignore').decode()
    return {w for w in re.findall(r'[a-z0-9]+', t) if w not in STOP}


def parse_ics(text):
    """VEVENTs as dicts {summary, description, categories, start: date, end: date}. Minimal RFC 5545 reader."""
    lines = re.sub(r'\r?\n[ \t]', '', text).splitlines()  # unfold
    events, ev = [], None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            ev = {}
        elif line == 'END:VEVENT' and ev is not None:
            if 'start' in ev:
                ev.setdefault('end', ev['start'])
                events.append(ev)
            ev = None
        elif ev is not None and ':' in line:
            head, value = line.split(':', 1)
            name, params = head.split(';')[0].upper(), head.upper()
            value = value.replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';')
            if name in ('SUMMARY', 'DESCRIPTION', 'CATEGORIES'):
                ev[name.lower()] = value
            elif name in ('DTSTART', 'DTEND'):
                d = to_date(value)
                if name == 'DTEND' and 'VALUE=DATE' in params:
                    d -= timedelta(days=1)  # all-day DTEND is exclusive
                ev['start' if name == 'DTSTART' else 'end'] = d
    return events


def to_date(value):
    if len(value) == 8:
        return datetime.strptime(value, '%Y%m%d').date()
    dt = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(TZ).date()
    return dt.date()  # TZID=Europe/Paris or floating: already local


# Moodle sends one event when a test opens and one when it closes: merged into one item
PHASE = re.compile(r"\s+(s[’']ouvre|se termine|doit [eê]tre achev[ée]e?)\s*$", re.I)


def ecue_for(text, ecues):
    """Id of the course whose title words are all in the text, or whose id words are, else a course-code
    alias, else None. ecues: {'ecues': {id: title}, 'aliases': {code word: id}}."""
    have = words(text)
    best = None
    for cid, title in ecues.get('ecues', {}).items():
        need = words(title)
        if (need and need <= have or words(cid) <= have) and (best is None or len(need) > best[0]):
            best = (len(need), cid)
    return best[1] if best else next((v for k, v in ecues.get('aliases', {}).items() if k in have), None)


MONTHS = ['janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin', 'juillet', 'aout', 'septembre', 'octobre', 'novembre', 'decembre']
_DAY = r'(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?'
OPENING = re.compile(r'^ouverture le \w+ (\d{1,2}) (\w+) (\d{4}), \d\d:\d\d : (.+)$', re.I)
DATE_WORDS = re.compile(_DAY + r'(\d{1,2})(?:er)?\s+(' + '|'.join(MONTHS) + r')(?:\s+(\d{4}))?', re.I)
WEEK_OF = re.compile(r'semaine du (\d{1,2})(?:/(\d{1,2})|(?:er)? (' + '|'.join(MONTHS) + '))', re.I)
GRADES = re.compile(r'vos notes|notes? sur 20|notes? (?:d.examen )?disponibles?', re.I)  # results mails: private, no date
DATE_NUM = re.compile(_DAY + r'(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])', re.I)
EXAM_SENTENCE = re.compile(r'aura lieu|auront lieu|examen|exam\b|partiel|test|qcm|contr[oô]le', re.I)


def _ascii(text):
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()


def _year(day, month, sent, year=None):
    """Date from day/month; without a year, the one closest to the mail (announcements point ahead)."""
    try:
        if year:
            return date(int(year) + (2000 if int(year) < 100 else 0), month, day)
        d = date(sent.year, month, day)
        return d.replace(year=d.year + 1) if d < sent - timedelta(days=60) else d
    except ValueError:
        return None


def mail_event(subject, body, sent):
    """Pseudo-iCal event {summary, categories, start, end} from one Moodle mail (French), else None.
    subject: decoded Subject; body: plain text; sent: date of the mail."""
    subject = re.sub(r'\s+', ' ', re.sub(r'^\s*\[EPITA\]\s*', '', subject)).strip()  # \s+ also eats non-breaking spaces
    if re.match(r'^(re|tr|fwd?)\s*:', subject, re.I) or GRADES.search(subject + ' ' + body[:600]):
        return None
    m = OPENING.match(subject)  # « Ouverture le lundi 5 octobre 2026, 07:00 : Test 1 FR : ECUE DP »
    if m and _ascii(m[2]).lower() in MONTHS:
        d = date(int(m[3]), MONTHS.index(_ascii(m[2]).lower()) + 1, int(m[1]))
        return {'summary': m[4], 'categories': '', 'start': d, 'end': d}
    course, _, title = subject.partition(' : ') if ' : ' in subject else ('', '', subject)
    # the forum header repeats the post date (« par X, lundi 19 octobre 2026, 12:51 »): only the text after it
    text = body.split('-' * 20, 1)[-1] if '-' * 20 in body else body
    for chunk in [title] + [c for c in re.split(r'(?<=[.!?])\s+', text) if EXAM_SENTENCE.search(c)]:
        a = _ascii(chunk)
        if (w := WEEK_OF.search(a)) and (d := _year(int(w[1]), int(w[2]) if w[2] else MONTHS.index(w[3].lower()) + 1, sent)):
            return {'summary': title, 'categories': course, 'start': d, 'end': d + timedelta(days=4)}
        for rx in (DATE_WORDS, DATE_NUM):
            if (w := rx.search(a)):
                month = MONTHS.index(w[2].lower()) + 1 if rx is DATE_WORDS else int(w[2])
                d = _year(int(w[1]), month, sent, w[3]) if 1 <= month <= 12 else None
                if d and d >= sent:
                    return {'summary': title, 'categories': course, 'start': d, 'end': d}
    return None


def fetch_mails(since):
    """Pseudo-events from the mailbox (epita_mail.py), mails sent since `since`."""
    import email
    import email.header
    import email.utils
    import html
    import epita_mail
    dec = lambda h: str(email.header.make_header(email.header.decode_header(h or '')))  # noqa: E731
    conn = epita_mail.imap()
    conn.select('INBOX', readonly=True)
    ids = set()
    for w in ('examen', 'exam', 'partiel', 'controle', 'QCM', 'test', 'evaluation', 'ouverture', 'rattrapage'):
        ids |= set(conn.search(None, 'SINCE', since.strftime('%d-%b-%Y'), 'SUBJECT', f'"{w}"')[1][0].split())
    events = []
    for i in sorted(ids, key=int):
        msg = email.message_from_bytes(conn.fetch(i, '(BODY.PEEK[])')[1][0][1])
        body = ''
        for part in msg.walk():
            if part.get_content_type() in ('text/plain', 'text/html') and not part.get_filename():
                t = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
                body = html.unescape(re.sub(r'<[^>]+>', ' ', t)) if part.get_content_type() == 'text/html' else t
                break
        body = re.sub(r'\{mlang EN\}.*?\{mlang\}', ' ', body, flags=re.S)  # French part only
        sent = email.utils.parsedate_to_datetime(msg['Date']).astimezone(TZ).date()
        ev = mail_event(dec(msg['Subject']), re.sub(r'\s+', ' ', body), sent)
        if ev:
            events.append(ev)
    conn.logout()
    return events


def candidates(events, source, ecues, since):
    """Exam items {debut, fin?, titre, type, source, ecue?} from events, open/close pairs merged."""
    out = []
    for ev in events:
        text = ' '.join(ev.get(k, '') for k in ('summary', 'description', 'categories'))
        if ev['start'] < since or not EXAM.search(text):
            continue
        title = re.sub(r'\s*\((EVAL|EXAMEN)\)\s*$', '', ev.get('summary', '').strip(), flags=re.I)
        title = PHASE.sub('', title)
        kind = 'Examen' if re.search(r'type:\s*examen|\beval\b|examen|partiel|final|midterm', text, re.I) else 'Test à distance'
        item = {'debut': ev['start'].isoformat()}
        if ev['end'] != ev['start']:
            item['fin'] = ev['end'].isoformat()
        item.update({'titre': title, 'type': kind, 'source': source})
        ecue = ecue_for(text, ecues)
        if ecue:
            item['ecue'] = ecue
        same = next((o for o in out if o['titre'] == title), None)
        if same:  # « s'ouvre » + « se termine »: one item from the first start to the last end
            dates = sorted({same['debut'], same.get('fin', same['debut']), item['debut'], item.get('fin', item['debut'])})
            same['debut'], same['fin'] = dates[0], dates[-1]
            if same['fin'] == same['debut']:
                del same['fin']
        else:
            out.append(item)
    return out


def key(item):
    return item['debut'], tuple(sorted(words(item['titre'])))


def merge(known, found):
    """New items (not already listed on the same start date with the same title or type), deduplicated."""
    seen = {key(k) for k in known}
    typed = {(k['debut'], k['type']) for k in known}  # hand-written entries use other titles
    new, slots = [], set()
    # the same test often comes several times (Moodle feed, announcement, « Ouverture » mail, FR and EN):
    # an item without course is dropped when another one has the same date and type
    for f in sorted(found, key=lambda f: 'ecue' not in f):
        slot = (f['debut'], f['type'], f.get('ecue'))
        if key(f) in seen or (f['debut'], f['type']) in typed or slot in slots \
                or ('ecue' not in f and any(s[:2] == slot[:2] for s in slots)):
            continue
        seen.add(key(f))
        slots.add(slot)
        new.append(f)
    return sorted(new, key=lambda f: f['debut'])


def fetch_ics(url):
    with urlopen(Request(url, headers={'Accept': 'text/calendar', 'User-Agent': 'guetteur'}), timeout=30) as r:
        return r.read().decode('utf-8', 'replace')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='guetteur', description=__doc__.split('\n')[0])
    ap.add_argument('--ics', action='append', default=[], metavar='URL', help='iCal feed (repeatable; also $GUETTEUR_ICS)')
    ap.add_argument('--mail', action='store_true', help='also read the mailbox set up with `epita-mail login`')
    ap.add_argument('--ecues', type=Path, help='JSON {"ecues": {id: title}, "aliases": {word: id}} to tag each date')
    ap.add_argument('--merge', type=Path, metavar='FILE', help='JSON array file: add the new dates to it')
    ap.add_argument('--since', type=date.fromisoformat, help='ignore dates before (default: 1 September of the school year)')
    ap.add_argument('--json', action='store_true', help='print JSON instead of lines')
    ap.add_argument('--version', action='version', version=__version__)
    args = ap.parse_args(argv)
    urls = args.ics + os.environ.get('GUETTEUR_ICS', '').split()
    if not urls and not args.mail:
        ap.error('give at least one --ics URL (or $GUETTEUR_ICS) or --mail')
    ecues = json.loads(args.ecues.read_text(encoding='utf-8')) if args.ecues else {}
    today = date.today()
    since = args.since or date(today.year if today.month >= 9 else today.year - 1, 9, 1)
    found = []
    for url in urls:
        source = urlparse(url).hostname or 'ics'  # never print the URL: it holds a secret token
        try:
            text = fetch_ics(url)
        except OSError as e:
            print(f'{source}: {e}. Expired URL?', file=sys.stderr)
            continue
        if 'BEGIN:VCALENDAR' not in text:
            print(f'{source}: not an iCal feed. Expired URL?', file=sys.stderr)
            continue
        evs = candidates(parse_ics(text), source, ecues, since)
        print(f'{source}: {len(evs)} exams or tests since {since}.', file=sys.stderr)
        found += evs
    if args.mail:
        try:
            evs = candidates(fetch_mails(since), 'mail', ecues, since)
            print(f'mail: {len(evs)} exams or tests since {since}.', file=sys.stderr)
            found += evs
        except SystemExit as e:  # no token yet, or Microsoft refused it: the feeds still count
            print(f'mail skipped: {e}', file=sys.stderr)
    known = json.loads(args.merge.read_text(encoding='utf-8')) if args.merge and args.merge.exists() else []
    new = merge(known, found)
    if args.merge and new:
        args.merge.write_text(json.dumps(sorted(known + new, key=lambda i: i['debut']), ensure_ascii=False, indent=1) + '\n',
                              encoding='utf-8')
    if args.json:
        print(json.dumps(new, ensure_ascii=False, indent=1))
    else:
        for n in new:
            print(f"{n['debut']}{' → ' + n['fin'] if 'fin' in n else ''}  {n['type']:16} {n['titre']}  [{n.get('ecue', '?')}]")
        if args.merge:
            print(f'{len(new)} new date(s) added to {args.merge}.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
