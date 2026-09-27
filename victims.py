#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
structured victim extraction & enrichment
parses the saved leak site source (see ransomwatch.py scrape) with per-group structured parsers,
enriches each victim (country, industry, data types, company size) and writes flat tables to data/
  data/victims.json          - source of truth, upserted on every run
  data/victims.csv           - one row per victim (for tableau & co)
  data/victim_data_types.csv - one row per victim per data type (long format, for breakdowns)
every enriched field carries a *_source column so site-provided values can be told apart from inferred ones
description is what the group says about the victim, leak_claim is what the group says it stole -
data types & leak subject are only ever derived from leak_claim, never from the company description
'''
import os
import re
import csv
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

import requests
import tldextract
import pycountry
from bs4 import BeautifulSoup

from sharedutils import openjson, striptld, socksfetcher, oproxies, headers
from sharedutils import stdlog, errlog

DATADIR = 'data'
DETAILDIR = os.path.join('source', 'detail')
JSONFILE = os.path.join(DATADIR, 'victims.json')
CSVFILE = os.path.join(DATADIR, 'victims.csv')
TYPESFILE = os.path.join(DATADIR, 'victim_data_types.csv')
# the table only covers attacks from this date on
CUTOFF = '2026-01-01'

COLUMNS = [
    'group', 'victim', 'website', 'date', 'date_source', 'published', 'first_seen', 'last_seen',
    'country', 'country_name', 'country_source',
    'activity', 'activity_raw', 'activity_source',
    'revenue_usd', 'revenue_band', 'employees', 'employee_band',
    'data_types', 'leak_subject', 'data_size', 'data_files', 'encrypted', 'status', 'views',
    'description', 'leak_claim', 'leak_claim_generic', 'post_url', 'source_url',
]

def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def ms_to_date(ms):
    if not ms or ms < 0:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d')

def s_to_date(sec):
    if not sec or sec < 0:
        return None
    return datetime.fromtimestamp(sec, timezone.utc).strftime('%Y-%m-%d')

def clean(text):
    if text is None:
        return None
    text = re.sub(r'\s+', ' ', str(text).replace('\xa0', ' ')).strip()
    return text or None

def base_url(slug):
    parts = urlsplit(slug)
    return parts.scheme + '://' + parts.netloc

def domain_from(text):
    '''first thing in a string that looks like a domain'''
    if not text:
        return None
    for token in re.split(r'[\s,;|]+', text):
        token = token.strip().lower()
        token = re.sub(r'^https?://', '', token).split('/')[0]
        token = re.sub(r'^www\.', '', token)
        ext = tldextract.extract(token)
        if ext.domain and ext.suffix:
            return ext.domain + '.' + ext.suffix
    return None

def to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None

# first line of a post that starts describing the stolen data rather than the victim
CLAIM_START_RE = re.compile(r'\b(leak(ed|age)?|stolen|exfiltrat\w*|data\s*(type)?\s*:|full data|types of information'
                            r'|access has been gained|confidential files|we will upload|will be (uploaded|published))',
                            re.IGNORECASE)
# never keep download locations or archive passwords, only the description of what was taken
CLAIM_NOISE_RE = re.compile(r'(https?://\S+|\S+\.onion\S*|magnet:\S+|(rar |zip |archive )?password\s*:.*$)', re.IGNORECASE)
# descriptions keep the victim's own clearnet links but lose anything pointing at the stolen data
DOWNLOAD_NOISE_RE = re.compile(r'(\S+\.onion\S*|magnet:\S+|magnet url\s*:?|\bpassword\b\s*:?\s*\S+)', re.IGNORECASE)

def split_claim(lines):
    '''split post text into (victim description, leak claim) at the first line that talks about the data'''
    lines = [line for line in (clean(line) for line in lines) if line]
    for index, line in enumerate(lines):
        if CLAIM_START_RE.search(line):
            return clean(' '.join(lines[:index])), clean(' '.join(lines[index:]))
    return clean(' '.join(lines)), None

def strip_noise(text):
    return clean(CLAIM_NOISE_RE.sub('', text)) if text else None

def strip_downloads(text):
    return clean(DOWNLOAD_NOISE_RE.sub('', text)) if text else None

'''
per-group parsers
each takes the saved page content & the location slug it was fetched from,
and returns a list of raw victim dicts using the keys in COLUMNS (anything missing is left None)
'''

def parse_incransom(content, slug):
    data = json.loads(content)
    victims = []
    for post in data['payload']['announcements']:
        company = post.get('company') or {}
        name = clean(unquote(company.get('company_name', '')))
        description, claim = split_claim(unquote(line) for line in post.get('description', []))
        victims.append({
            'victim': name,
            'website': domain_from(name),
            'country': company.get('country'),
            'revenue_usd': to_int(company.get('revenue')),
            'description': description,
            'leak_claim': strip_noise(claim),
            'published': ms_to_date(post.get('createdAt')),
            'views': post.get('visits'),
            'status': ','.join(post.get('categories') or []) or None,
        })
    return victims

def parse_qilin(content, slug):
    soup = BeautifulSoup(content, 'html.parser')
    victims = []
    for item in soup.select('div.item_box'):
        title = item.select_one('a.item_box-title')
        if title is None:
            continue
        website = None
        for link in item.select('a.item_box-info__link'):
            if 'company url' in link.get_text(strip=True).lower():
                website = domain_from(link.get('href'))
        industry = item.select_one('p.item_box-info')
        published = None
        clock = item.find('img', src=re.compile('clock'))
        if clock is not None:
            try:
                published = datetime.strptime(clean(clock.parent.get_text()), '%b %d, %Y').strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                published = None
        # published leaks show a file count & total size on the card, qilin posts carry no text beyond that
        card = item.get_text(' ', strip=True)
        files = re.search(r'(\d+)\s+files?\b', card)
        victims.append({
            'victim': clean(title.get_text()),
            'website': website,
            'activity_raw': clean(industry.get_text()) if industry else None,
            'data_size': data_size_from(card),
            'data_files': int(files.group(1)) if files else None,
            'published': published,
            'status': 'published' if item.select_one('p.publicated') else 'pending',
            'post_url': base_url(slug) + title.get('href') if title.get('href') else None,
        })
    return victims

def parse_play(content, slug):
    soup = BeautifulSoup(content, 'html.parser')
    victims = []
    for cell in soup.select('th.News'):
        parts = [clean(p) for p in cell.get_text('|').split('|')]
        parts = [p for p in parts if p]
        if len(parts) < 3:
            continue
        record = {'victim': parts[0], 'country': parts[1], 'website': domain_from(parts[2])}
        for part in parts[3:]:
            lower = part.lower()
            if 'views:' in lower:
                record['views'] = to_int(lower.split('views:')[1])
            elif lower.startswith('added:'):
                record['published'] = part.split(':', 1)[1].strip()
            elif part.isupper():
                record['status'] = lower
        topic = re.search(r"viewtopic\('([^']+)'\)", cell.get('onclick', ''))
        if topic:
            record['post_url'] = base_url(slug) + '/topic.php?id=' + topic.group(1)
        victims.append(record)
    return victims

def parse_safepay(content, slug):
    soup = BeautifulSoup(content, 'html.parser')
    victims = []
    for card in soup.select('div.card'):
        title = card.select_one('h5.card-title')
        if title is None:
            continue
        flag = card.select_one('img.country-flag')
        text = card.select_one('p.card-text')
        views = card.select_one('span.badge')
        link = card.find('a', string=re.compile('Learn More'))
        victims.append({
            'victim': clean(title.get_text()),
            'website': domain_from(title.get_text()),
            'country': flag.get('alt') if flag else None,
            'description': clean(text.get_text()) if text else None,
            'views': to_int(clean(views.get_text())) if views else None,
            'status': 'published' if card.select_one('.published-text') else 'pending',
            'post_url': base_url(slug) + link.get('href') if link else None,
        })
    return victims

AKIRA_WRAP = 94

def unwrap(text):
    '''akira hard-wraps posts at 95 columns, often mid-word - rejoin into paragraphs'''
    paragraphs = []
    for block in (text or '').split('\n\n'):
        joined, previous = '', ''
        for line in block.split('\n'):
            joined += ('' if not joined or len(previous) >= AKIRA_WRAP else ' ') + line
            previous = line
        paragraphs.append(joined)
    return paragraphs

def parse_akira(content, slug):
    '''
    akira has two json feeds - news (/n) announces a victim & says what was taken,
    leaks (/l) lists victims whose data is out. the leaks feed carries a download link which is never read
    '''
    data = json.loads(content)
    victims = []
    for post in data['objects']:
        if 'content' in post:
            description, claim = split_claim(unwrap(post['content']))
            victims.append({
                'victim': clean(post.get('title')),
                'description': description,
                'leak_claim': strip_noise(claim),
                'published': post.get('date'),
                'status': 'pending',
            })
        else:
            # desc here is a truncated copy of the news post, so it is not kept
            victims.append({
                'victim': clean(post.get('name')),
                'published': post.get('date'),
                'status': 'published',
            })
    return victims

def parse_krybit(content, slug):
    '''krybit lists every post on one page with no dates - the post page carries the date (see parse_krybit_detail)'''
    soup = BeautifulSoup(content, 'html.parser')
    victims = []
    for card in soup.select('div.post-card'):
        title = card.select_one('.post-title')
        if title is None:
            continue
        status = card.select_one('.post-status')
        views = card.select_one('.post-views')
        link = re.search(r"window\.location='([^']+)'", card.get('onclick', ''))
        victims.append({
            'victim': clean(title.get_text()),
            'website': domain_from(title.get_text()),
            'status': 'published' if status and 'published' in status.get('class', []) else 'pending',
            'views': to_int(clean(views.get_text()).replace(',', '')) if views else None,
            'post_url': base_url(slug) + link.group(1) if link else None,
        })
    return victims

'''
per-group detail parsers
the listing pages only carry a summary, the post page is where groups describe what they took
each takes a post page & returns the extra fields it found
'''

def parse_play_detail(content):
    soup = BeautifulSoup(content, 'html.parser')
    fields = {}
    # only these labelled lines are read - the rest of the post is download links & archive passwords
    for line in soup.get_text('\n').split('\n'):
        label, _, value = line.partition(':')
        label, value = label.strip().lower(), clean(value)
        if not value:
            continue
        if label == 'amount of data':
            fields['data_size'] = data_size_from(value)
        elif label == 'information':
            fields['activity_raw'] = value
        elif label == 'comment':
            fields['leak_claim'] = strip_noise(value)
    return fields

# "$12-15 million" keeps the lower bound, with the unit that follows the range
MONEY_RE = re.compile(r'\$\s*([\d.,]+)(?:\s*[–-]\s*[\d.,]+)?\s*(billion|bn|b|million|mn|m|thousand|k)?\b', re.IGNORECASE)
MONEY_SCALE = {'billion': 1e9, 'bn': 1e9, 'b': 1e9, 'million': 1e6, 'mn': 1e6, 'm': 1e6, 'thousand': 1e3, 'k': 1e3}

def money_from(text):
    match = MONEY_RE.search(text or '')
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(',', ''))
    except ValueError:
        return None
    return int(amount * MONEY_SCALE.get((match.group(2) or '').lower(), 1))

KRYBIT_LABEL_RE = re.compile(r'^\s*([A-Za-z][\w ()&/.\-]{0,40}?)\s*:\s*(.+)$')
KRYBIT_IMAGE_RE = re.compile(r'/content/(\d{10})(?:\.\d+)?-')

def parse_krybit_detail(content):
    soup = BeautifulSoup(content, 'html.parser')
    fields = {}
    # proof screenshots are named after their upload time - the earliest one is when the post went up.
    # the unlock time is the deadline the group set, only used if a post has no screenshots
    uploads = [int(m.group(1)) for img in soup.select('img.gallery-item')
               for m in [KRYBIT_IMAGE_RE.search(img.get('src', ''))] if m]
    unlock = soup.select_one('#unlock-time')
    unlock_date = re.search(r'\d{4}-\d{2}-\d{2}', unlock.get_text()) if unlock else None
    fields['published'] = s_to_date(min(uploads)) if uploads else (unlock_date.group(0) if unlock_date else None)
    # the post is company prose followed by labelled contact & company lines - only the prose and
    # the sector, headcount & data size labels are kept, never the addresses, phone numbers or emails
    prose, in_prose = [], True
    for paragraph in soup.select('.article-content p'):
        text = clean(paragraph.get_text(' '))
        if not text:
            continue
        label = KRYBIT_LABEL_RE.match(text)
        if label:
            in_prose = False
            key, value = label.group(1).strip().lower(), label.group(2)
            if key == 'sector':
                fields['activity_raw'] = clean(value)
            elif key == 'employees':
                # often a range ("201-500") or "3,325+" - the lower bound is kept
                number = re.search(r'\d[\d,]*', value)
                fields['employees'] = to_int(number.group(0).replace(',', '')) if number else None
            elif key == 'revenue':
                # several estimates are sometimes given, the first is kept
                fields['revenue_usd'] = money_from(value)
            elif key.endswith('data'):
                fields['data_size'] = data_size_from(value)
        elif in_prose:
            prose.append(text)
    fields['description'] = clean(' '.join(prose)) if prose else None
    return fields

def parse_safepay_detail(content):
    soup = BeautifulSoup(content, 'html.parser')
    fields = {}
    meta = soup.select_one('.card-body p.text-muted')
    posted = re.search(r'\d{4}-\d{2}-\d{2}', meta.get_text()) if meta else None
    if posted:
        fields['published'] = posted.group(0)
    paragraphs = [p.get_text(' ') for p in soup.select('.card-body div.mb-3 p')]
    description, claim = split_claim(paragraphs)
    fields['description'] = description
    fields['leak_claim'] = strip_noise(claim)
    return fields

PARSERS = {
    'incransom': parse_incransom,
    'qilin': parse_qilin,
    'play': parse_play,
    'safepay': parse_safepay,
    'akira': parse_akira,
    'krybit': parse_krybit,
}

DETAIL_PARSERS = {
    'play': parse_play_detail,
    'safepay': parse_safepay_detail,
    'krybit': parse_krybit_detail,
}

# how to request page n of each of a group's victim feeds, given one of its location slugs
# feeds are walked in order & later ones win on merge - akira's leaks feed goes first so the news feed's
# announcement date wins over the leak date, while merge keeps the published status
PAGERS = {
    'incransom': [lambda slug, n: base_url(slug) + '/api/v1/blog/get/announcements?page=' + str(n) + '&perPage=100'],
    'qilin': [lambda slug, n: base_url(slug) + '/?page=' + str(n)],
    'play': [lambda slug, n: base_url(slug) + '/index.php?page=' + str(n)],
    'safepay': [lambda slug, n: base_url(slug) + '/?page=' + str(n)],
    'akira': [lambda slug, n: base_url(slug) + '/l?page=' + str(n) + '&sort=date%3Adesc',
              lambda slug, n: base_url(slug) + '/n?page=' + str(n) + '&sort=date%3Adesc'],
    # krybit has no pagination, every post is on its home page
    'krybit': [lambda slug, n: base_url(slug) + '/' if n == 1 else None],
}

'''
enrichment
'''

GENERIC_TLDS = {'co', 'io', 'ai', 'me', 'tv', 'cc', 'ws', 'fm', 'am', 'la', 'to', 'eu', 'asia', 'app', 'dev'}
COUNTRY_ALIASES = {'uk': 'GB', 'usa': 'US', 'u.s.': 'US', 'united states of america': 'US', 'america': 'US',
                   'england': 'GB', 'scotland': 'GB', 'wales': 'GB', 'uae': 'AE', 'south korea': 'KR',
                   'korea': 'KR', 'russia': 'RU', 'vietnam': 'VN', 'taiwan': 'TW', 'czech republic': 'CZ',
                   'turkey': 'TR', 'iran': 'IR', 'bolivia': 'BO', 'venezuela': 'VE', 'tanzania': 'TZ'}

def _country_names():
    names = dict(COUNTRY_ALIASES)
    for country in pycountry.countries:
        names[country.name.lower()] = country.alpha_2
        if hasattr(country, 'common_name'):
            names[country.common_name.lower()] = country.alpha_2
    return names

COUNTRY_NAMES = _country_names()
COUNTRY_TEXT_RE = re.compile(
    r'\b(' + '|'.join(re.escape(n) for n in sorted(COUNTRY_NAMES, key=len, reverse=True) if len(n) > 3) + r')\b'
)

def normalise_country(value):
    '''country code or name (as a site presents it) -> iso alpha-2'''
    value = clean(value)
    if not value:
        return None
    lower = value.lower()
    if lower in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[lower]
    if len(value) == 2 and pycountry.countries.get(alpha_2=value.upper()):
        return value.upper()
    if lower in COUNTRY_NAMES:
        return COUNTRY_NAMES[lower]
    try:
        return pycountry.countries.lookup(value).alpha_2
    except LookupError:
        return None

def country_from_tld(website):
    if not website:
        return None
    suffix = tldextract.extract(website).suffix.split('.')[-1]
    if len(suffix) != 2 or suffix in GENERIC_TLDS:
        return None
    return normalise_country(suffix)

def country_from_text(text):
    if not text:
        return None
    match = COUNTRY_TEXT_RE.search(text.lower())
    return COUNTRY_NAMES[match.group(1)] if match else None

INDUSTRIES = {
    'Healthcare': ['hospital', 'clinic', 'medical', 'health', 'healthcare', 'pharma', 'pharmaceutical', 'dental',
                   'dentist', 'surgery', 'patient', 'laboratory', 'laboratories', 'biotech', 'nursing', 'physician',
                   'orthopedic', 'radiology', 'veterinary', 'therapy', 'senior living', 'senior services',
                   'care home', 'drug', 'caring'],
    'Education': ['school', 'schools', 'university', 'college', 'academy', 'education', 'educational', 'campus',
                  'students', 'kindergarten', 'school district'],
    'Government': ['city of', 'county', 'municipality', 'municipal', 'government', 'ministry', 'council',
                   'state of', 'department of', 'township', 'police', 'court', 'defence force', 'defense force',
                   'military', 'public institutions', 'gob', 'gov'],
    'Financial Services': ['bank', 'banking', 'credit union', 'insurance', 'insurer', 'financial', 'finance',
                           'investment', 'investments', 'asset management', 'wealth', 'mortgage', 'lending',
                           'securities', 'fintech', 'brokerage', 'inversiones'],
    'Technology': ['software', 'it services', 'technology', 'technologies', 'cloud', 'saas', 'it solutions',
                   'cyber', 'data center', 'computer', 'systems integrator', 'telecom', 'telecommunications',
                   'internet', 'digital'],
    'Manufacturing': ['manufacturing', 'manufacturer', 'manufactures', 'industrial', 'factory', 'machinery',
                      'equipment', 'plastic', 'metal', 'tool', 'tooling', 'machine', 'steel', 'chemical', 'chemicals', 'automotive',
                      'components', 'fabrication', 'packaging', 'textile', 'electronics', 'aerospace', 'composites',
                      'compositech'],
    'Construction': ['construction', 'contractor', 'contractors', 'builders', 'civil engineering', 'architecture',
                     'architects', 'roofing', 'plumbing', 'hvac', 'concrete', 'construtora'],
    'Business Services': ['law firm', 'attorneys', 'attorney', 'lawyers', 'legal', 'consulting', 'consultancy',
                          'consultants', 'accounting', 'accountants', 'cpa', 'staffing', 'recruitment',
                          'marketing', 'advertising', 'outsourcing', 'engineers', 'professional services'],
    'Retail & Consumer': ['retail', 'retailer', 'store', 'stores', 'shop', 'e-commerce', 'fashion', 'apparel',
                          'consumer', 'supermarket', 'furniture', 'jewelry', 'cosmetics', 'wholesale',
                          'distributor', 'distribution'],
    'Transportation & Logistics': ['logistics', 'transport', 'transportation', 'trucking', 'freight', 'shipping',
                                   'airline', 'airport', 'aviation', 'rail', 'courier', 'fleet'],
    'Energy & Utilities': ['energy', 'oil', 'gas', 'petroleum', 'power', 'utilities', 'utility', 'water',
                           'wastewater', 'solar', 'renewable', 'mining', 'electricity'],
    'Hospitality & Leisure': ['hotel', 'hotels', 'resort', 'restaurant', 'restaurants', 'hospitality', 'tourism',
                              'travel', 'casino', 'golf', 'country club', 'entertainment', 'media'],
    'Agriculture & Food': ['agriculture', 'agricultural', 'agricole', 'farm', 'farms', 'farming', 'food', 'foods',
                           'beverage', 'dairy', 'meat', 'seafood', 'bakery', 'brewery', 'winery'],
    'Real Estate': ['real estate', 'property', 'properties', 'realty', 'housing'],
}
INDUSTRY_RES = {name: re.compile(r'\b(' + '|'.join(re.escape(k) for k in words) + r')(?:s|es)?\b')
                for name, words in INDUSTRIES.items()}

def classify(text, patterns):
    '''highest keyword hit count wins, ties go to the earlier category'''
    if not text:
        return None
    text = text.lower()
    best, best_hits = None, 0
    for name, pattern in patterns.items():
        hits = len(pattern.findall(text))
        if hits > best_hits:
            best, best_hits = name, hits
    return best

DATA_TYPES = {
    'Personal data (PII)': ['personal data', 'personal information', 'pii', 'passport', 'passports', 'id card',
                            'id cards', 'driver license', "driver's license", 'ssn', 'social security',
                            'date of birth', 'home addresses', 'ids', "id's", 'personal files', 'personal documents',
                            'private data', 'personal confidential'],
    'Employee / HR': ['employee', 'employees', 'hr', 'human resources', 'payroll', 'personnel', 'salary',
                      'salaries', 'staff data', 'resumes', 'disciplinary', 'staff'],
    'Financial': ['financial', 'finance', 'accounting', 'bank statements', 'invoices', 'invoice', 'tax', 'taxes',
                  'budget', 'balance sheet', 'payments', 'credit card', 'audit', 'bank details'],
    'Customer data': ['customer', 'customers', 'client', 'clients', 'crm', 'patients', 'students',
                      'members', 'policyholders', 'guests', 'tenants'],
    'Medical (PHI)': ['medical records', 'patient', 'patients', 'phi', 'health records', 'diagnosis',
                      'prescriptions', 'medical data'],
    'Legal / contracts': ['contract', 'contracts', 'agreement', 'agreements', 'nda', 'litigation', 'case files',
                          'legal documents', 'lawsuit', 'discovery'],
    'Intellectual property / technical': ['source code', 'blueprint', 'blueprints', 'drawings', 'cad', 'r&d',
                                          'research', 'patent', 'patents', 'formula', 'formulas', 'technical',
                                          'engineering', 'designs', 'product data', 'quality control',
                                          'certification'],
    'Credentials / IT': ['password', 'passwords', 'credentials', 'database', 'databases', 'backup', 'backups',
                         'sql', 'active directory', 'server'],
    'Corporate confidential': ['confidential', 'corporate', 'internal documents', 'board', 'correspondence',
                               'emails', 'email', 'mail', 'strategy', 'nda', 'counterparties'],
}
DATA_TYPE_RES = {name: re.compile(r'\b(' + '|'.join(re.escape(k) for k in words) + r')(?:s|es)?\b')
                 for name, words in DATA_TYPES.items()}

# who each data type is about - the question a victim's customers & staff care about
SUBJECTS = {
    'Customer data': 'Customers',
    'Medical (PHI)': 'Customers',
    'Employee / HR': 'Employees',
    'Personal data (PII)': 'Individuals (unspecified)',
}
COMPANY_SUBJECT = 'Company'

def subject_of(data_type):
    return SUBJECTS.get(data_type, COMPANY_SUBJECT)

def leak_subjects(data_types):
    '''ordered subjects for a victim, personal data only counts as unspecified when nobody else is named'''
    subjects = []
    for subject in ('Customers', 'Employees', 'Individuals (unspecified)', COMPANY_SUBJECT):
        if subject in {subject_of(t) for t in data_types}:
            subjects.append(subject)
    if 'Customers' in subjects or 'Employees' in subjects:
        subjects = [s for s in subjects if s != 'Individuals (unspecified)']
    return subjects

def data_types_from(text):
    if not text:
        return []
    text = text.lower()
    return [name for name, pattern in DATA_TYPE_RES.items() if pattern.search(text)]

DATA_SIZE_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(tb|gb|mb)\b', re.IGNORECASE)

def data_size_from(text):
    if not text:
        return None
    match = DATA_SIZE_RE.search(text)
    return match.group(1).replace(',', '.') + ' ' + match.group(2).upper() if match else None

def band(value, bands):
    if value is None:
        return None
    for limit, label in bands:
        if value < limit:
            return label
    return bands[-1][1]

REVENUE_BANDS = [(10_000_000, '<$10M'), (50_000_000, '$10M-50M'), (250_000_000, '$50M-250M'),
                 (1_000_000_000, '$250M-1B'), (float('inf'), '>$1B')]
EMPLOYEE_BANDS = [(50, '<50'), (250, '50-249'), (1000, '250-999'), (5000, '1,000-4,999'), (float('inf'), '5,000+')]

def enrich(record):
    text = ' '.join(filter(None, [record.get('victim'), record.get('description')]))

    country = normalise_country(record.get('country'))
    source = 'site' if country else None
    if not country:
        country = country_from_tld(record.get('website'))
        source = 'tld' if country else None
    if not country:
        country = country_from_text(record.get('description'))
        source = 'text' if country else None
    record['country'] = country
    record['country_source'] = source
    record['country_name'] = pycountry.countries.get(alpha_2=country).name if country else None

    activity = classify(record.get('activity_raw'), INDUSTRY_RES)
    source = 'site' if activity else None
    if not activity:
        activity = classify(' '.join(filter(None, [text, record.get('website')])), INDUSTRY_RES)
        source = 'keyword' if activity else None
    record['activity'] = activity or 'Unknown'
    record['activity_source'] = source

    record['data_size'] = record.get('data_size') or data_size_from(record.get('leak_claim'))
    record['revenue_band'] = band(record.get('revenue_usd'), REVENUE_BANDS)
    record['employee_band'] = band(record.get('employees'), EMPLOYEE_BANDS)
    return classify_leak(record)

def classify_leak(record):
    '''data types & subject from the group's claim only - a victim with no claim stays blank, not guessed'''
    types = data_types_from(record.get('leak_claim'))
    record['data_types'] = ';'.join(types) or None
    if types:
        record['leak_subject'] = ';'.join(leak_subjects(types))
    else:
        record['leak_subject'] = 'Unclassified' if record.get('leak_claim') else None
    return record

'''
table building
'''

def victimkey(group, victim):
    return group + '|' + re.sub(r'[^a-z0-9]', '', (victim or '').lower())

def load_existing():
    if not os.path.exists(JSONFILE):
        return {}
    return {victimkey(r['group'], r['victim']): r for r in openjson(JSONFILE)}

def collect():
    '''run the structured parsers over every saved source page for supported groups'''
    found = []
    for group in openjson('groups.json'):
        parser = PARSERS.get(group['name'])
        if parser is None:
            continue
        for host in group['locations']:
            filename = os.path.join('source', group['name'] + '-' + striptld(host['slug']) + '.html')
            if not os.path.exists(filename) or os.path.getsize(filename) == 0:
                continue
            with open(filename, encoding='utf-8', errors='ignore') as sourcefile:
                content = sourcefile.read()
            try:
                victims = parser(content, host['slug'])
            except (ValueError, KeyError, TypeError) as error:
                # json/api groups serve a html error or challenge page when their backend is down
                errlog('victims: ' + group['name'] + ' - could not parse ' + filename + ' - ' + str(error))
                continue
            stdlog('victims: ' + group['name'] + ' - ' + str(len(victims)) + ' victims from ' + filename)
            for victim in victims:
                if victim.get('victim'):
                    victim['group'] = group['name']
                    victim['source_url'] = host['slug']
                    found.append(victim)
    return found

GENERIC_CLAIM_MIN = 5

def merge(existing, found):
    timestamp = now()
    for victim in found:
        key = victimkey(victim['group'], victim['victim'])
        record = existing.get(key, {'first_seen': timestamp})
        # a leak can't be taken back, so a published victim seen again in a pending feed stays published
        if record.get('status') == 'published' and victim.get('status') == 'pending':
            victim = {k: v for k, v in victim.items() if k != 'status'}
        # keep previously captured values if this page no longer shows them
        record.update({k: v for k, v in victim.items() if v not in (None, '')})
        record['last_seen'] = timestamp
        existing[key] = enrich(record)
    # a claim repeated word for word across a group's posts is a template, not a description of that victim
    claims = {}
    for record in existing.values():
        if record.get('leak_claim'):
            key = (record['group'], record['leak_claim'][:100].lower())
            claims[key] = claims.get(key, 0) + 1
    for record in existing.values():
        record['description'] = strip_downloads(record.get('description'))
        record['leak_claim'] = strip_downloads(record.get('leak_claim'))
        classify_leak(record)
        if record.get('leak_claim'):
            record['leak_claim_generic'] = claims[(record['group'], record['leak_claim'][:100].lower())] >= GENERIC_CLAIM_MIN
        # a scheduled release date (krybit's countdown) can lie in the future, but a post can't be dated
        # later than the day we first saw it
        record['date'] = min(record['published'], record['first_seen'][:10]) if record.get('published') \
            else record['first_seen'][:10]
        record['date_source'] = 'site' if record.get('published') else 'first_seen'
    # only attacks the group itself dated on or after the cutoff are kept - a first_seen date is when we
    # happened to scrape the post, which for an undated listing can be years after the attack
    kept = [r for r in existing.values() if r['date_source'] == 'site' and r['date'] >= CUTOFF]
    undated = sum(1 for r in existing.values() if r['date_source'] != 'site')
    stdlog('victims: kept ' + str(len(kept)) + ' victims dated ' + CUTOFF + ' or later, dropped '
           + str(len(existing) - len(kept) - undated) + ' older & ' + str(undated) + ' with no site date')
    return sorted(kept, key=lambda r: (r['date'], r['group'], r['victim']), reverse=True)

def write(records):
    os.makedirs(DATADIR, exist_ok=True)
    with open(JSONFILE, 'w', encoding='utf-8') as jsonfile:
        json.dump(records, jsonfile, ensure_ascii=False, indent=4)
    with open(CSVFILE, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    with open(TYPESFILE, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['group', 'victim', 'date', 'country', 'activity', 'data_type', 'subject'])
        for r in records:
            for data_type in (r.get('data_types') or '').split(';'):
                if data_type:
                    writer.writerow([r['group'], r['victim'], r['date'], r.get('country'), r['activity'], data_type,
                                     subject_of(data_type)])

'''
backfill
the scheduled scrape only saves the first page of each site, so history is shallow.
backfill walks each group's pagination back to a cutoff date, then fetches the post pages
where groups describe the stolen data. post pages are cached in source/detail/ since they rarely change,
listing pages are not since posts shift between pages as new victims are added
'''

def fetch(url, retries=3):
    for _ in range(retries):
        content = socksfetcher(url)
        if content:
            return content
    return None

def akira_fetcher(slug):
    '''akira only answers its json feeds for a session holding the csrf token from its home page'''
    session = requests.Session()
    session.proxies = oproxies
    session.verify = False
    session.headers.update(headers())
    try:
        home = session.get(base_url(slug) + '/', timeout=35).text
    except requests.exceptions.RequestException as error:
        errlog('victims: akira - could not open a session - ' + str(error))
        return lambda url: None
    token = re.search(r'name="csrf-token" content="([^"]+)"', home)
    session.headers.update({'X-CSRF-Token': token.group(1) if token else '', 'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json', 'Referer': base_url(slug) + '/'})
    def get(url):
        for _ in range(3):
            try:
                response = session.get(url, timeout=35)
                if response.text:
                    return response.text
            except requests.exceptions.RequestException as error:
                errlog('victims: akira - ' + url + ' - ' + str(error))
        return None
    return get

# groups whose listings need more than a plain socks request, given a location slug return a url -> content fetcher
FETCHERS = {
    'akira': akira_fetcher,
}

def fetch_detail(group, url):
    cache = os.path.join(DETAILDIR, group + '-' + hashlib.sha1(url.encode()).hexdigest()[:16] + '.html')
    if os.path.exists(cache) and os.path.getsize(cache) > 0:
        with open(cache, encoding='utf-8', errors='ignore') as cachefile:
            return cachefile.read()
    content = fetch(url)
    if content:
        os.makedirs(DETAILDIR, exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as cachefile:
            cachefile.write(content)
    return content

def add_details(group, victims, workers):
    parser = DETAIL_PARSERS.get(group)
    todo = [v for v in victims if v.get('post_url')] if parser else []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pages = pool.map(lambda v: fetch_detail(group, v['post_url']), todo)
        for victim, content in zip(todo, pages):
            if content is None:
                errlog('victims: ' + group + ' - could not fetch post ' + victim['post_url'])
                continue
            victim.update({k: v for k, v in parser(content).items() if v not in (None, '')})

def backfill_feed(group, host, pager, getter, since, maxpages, workers):
    parser = PARSERS[group['name']]
    found = []
    for page in range(1, maxpages + 1):
        url = pager(host['slug'], page)
        if url is None:
            break
        content = getter(url)
        try:
            victims = parser(content, host['slug']) if content else []
        except (ValueError, KeyError, TypeError):
            victims = []
        if not victims:
            break
        add_details(group['name'], victims, workers)
        for victim in victims:
            victim['group'] = group['name']
            victim['source_url'] = host['slug']
        found.extend(victims)
        dates = [v['published'] for v in victims if v.get('published')]
        stdlog('victims: backfill ' + group['name'] + ' page ' + str(page) + ' - ' + str(len(victims))
               + ' victims, oldest ' + str(min(dates) if dates else 'undated'))
        # listings are newest first, so once a page reaches past the cutoff we are done
        if dates and min(dates) < since:
            break
    return [v for v in found if v.get('victim') and (v.get('published') or since) >= since]

def backfill_group(group, since, maxpages, workers):
    for host in group['locations']:
        if host['enabled'] is False:
            continue
        getter = FETCHERS[group['name']](host['slug']) if group['name'] in FETCHERS else fetch
        found = []
        for pager in PAGERS[group['name']]:
            found.extend(backfill_feed(group, host, pager, getter, since, maxpages, workers))
        if found:
            return found
        errlog('victims: backfill ' + group['name'] + ' - nothing parsed from ' + host['slug'])
    return []

def backfill(since, maxpages=100, workers=6, groups=None):
    stdlog('victims: backfilling supported groups back to ' + since)
    existing = load_existing()
    found = []
    for group in openjson('groups.json'):
        if group['name'] not in PAGERS or (groups and group['name'] not in groups):
            continue
        victims = backfill_group(group, since, maxpages, workers)
        stdlog('victims: backfill ' + group['name'] + ' - ' + str(len(victims)) + ' victims since ' + since)
        found.extend(victims)
    records = merge(existing, found)
    write(records)
    stdlog('victims: ' + str(len(records)) + ' victims written to ' + CSVFILE)

def main():
    stdlog('victims: building structured victim table')
    records = merge(load_existing(), collect())
    write(records)
    stdlog('victims: ' + str(len(records)) + ' victims written to ' + CSVFILE)

if __name__ == '__main__':
    main()
