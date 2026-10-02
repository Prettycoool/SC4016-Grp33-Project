#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
structured victim extraction & enrichment
parses the saved leak site source (see ransomwatch.py scrape) with per-group structured parsers,
enriches each victim (country, industry, data types, company size) and writes flat tables to data/
  data/victims.json          - source of truth, upserted on every run
  data/victims.csv           - one row per victim (for tableau & co)
  data/victim_data_types.csv - one row per victim per data type (long format, for breakdowns)
  data/history.json/.csv     - same columns for posts dated before the cutoff, only filled by a deep backfill
  data/history_data_types.csv - long format data types for the history table
every enriched field carries a *_source column so site-provided values can be told apart from inferred ones
description is what the group says about the victim, leak_claim is what the group says it stole -
data types & leak subject are only ever derived from leak_claim, never from the company description
'''
import os
import re
import csv
import io
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urljoin

import numpy as np
import requests
import tldextract
from PIL import Image
import pycountry
from bs4 import BeautifulSoup

from sharedutils import openjson, striptld, socksfetcher, oproxies, headers
from sharedutils import stdlog, errlog

DATADIR = 'data'
DETAILDIR = os.path.join('source', 'detail')
LISTINGDIR = os.path.join('source', 'listing')
PROOFHASHDIR = os.path.join('source', 'proofhash')
PROOFDISTANCES = os.path.join(DATADIR, 'proof_distances.csv')
JSONFILE = os.path.join(DATADIR, 'victims.json')
CSVFILE = os.path.join(DATADIR, 'victims.csv')
TYPESFILE = os.path.join(DATADIR, 'victim_data_types.csv')
HISTJSON = os.path.join(DATADIR, 'history.json')
HISTCSV = os.path.join(DATADIR, 'history.csv')
HISTTYPES = os.path.join(DATADIR, 'history_data_types.csv')
# the table only covers attacks from this date on
CUTOFF = '2026-01-01'

COLUMNS = [
    'group', 'victim', 'website', 'date', 'date_source', 'published', 'first_seen', 'last_seen',
    'country', 'country_name', 'country_source',
    'activity', 'activity_raw', 'activity_source',
    'revenue_usd', 'revenue_band', 'revenue_source', 'employees', 'employee_band', 'employees_source',
    'data_types', 'data_types_source', 'leak_subject', 'listing_entries', 'data_size', 'data_files', 'proof_count', 'proof_ids', 'proof_sha256', 'proof_dhash',
    'encrypted', 'status', 'views',
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
                            r'|access has been gained|confidential files|we will upload|will be (uploaded|published)'
                            r'|ha(s|ve) collected such data)',
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
            # the tag is only ever added, so a post without it is unknown rather than not encrypted
            'encrypted': True if 'Encrypted' in (post.get('categories') or []) else None,
            # ids of the proof files on incransom's cdn - the same id under two posts is the same upload
            'proof_count': len(post.get('proof') or []) or None,
            'proof_ids': ';'.join(sorted(post.get('proof') or [])) or None,
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

QILIN_PHOTO_RE = re.compile(r'/photos/(?:thumbs/)?([0-9a-f]{32})\.\w+')

def parse_qilin_detail(content):
    soup = BeautifulSoup(content, 'html.parser')
    fields = {}
    # the stat row is a list of icon + value pairs, the icon says what the value is
    for icon in soup.select('div.item_box-info__item img'):
        value, kind = clean(icon.parent.get_text()), os.path.basename(icon.get('src', ''))
        if not value:
            continue
        if kind == 'eye.png':
            fields['views'] = to_int(value)
        elif kind == 'image.png':
            fields['proof_count'] = to_int(value.split()[0])
        elif kind == 'file-text.png':
            fields['data_files'] = to_int(value.split()[0])
        elif kind == 'download-cloud.png':
            fields['data_size'] = data_size_from(value)
    # proof photos are named by a 32 hex digest, kept so the same proof can be spotted under two posts
    # (the images themselves are never downloaded)
    photos = sorted(set(QILIN_PHOTO_RE.findall(content)))
    if photos:
        fields['proof_ids'] = ';'.join(photos)
    return fields

PARSERS = {
    'incransom': parse_incransom,
    'qilin': parse_qilin,
    'play': parse_play,
    'safepay': parse_safepay,
    'akira': parse_akira,
}

DETAIL_PARSERS = {
    'play': parse_play_detail,
    'safepay': parse_safepay_detail,
    'qilin': parse_qilin_detail,
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
                            'private data', 'personal confidential', 'dl', 'dl scans', 'drivers license',
                            'drivers licenses', 'dob', 'addresses', 'phone numbers', 'phones', 'birth certs',
                            'birth certificates', 'death certs', 'death certificates', 'visa', 'w-9', 'w9'],
    'Employee / HR': ['employee', 'employees', 'hr', 'human resources', 'payroll', 'personnel', 'salary',
                      'salaries', 'staff data', 'resumes', 'disciplinary', 'staff', 'i-9', 'i9'],
    'Financial': ['financial', 'finance', 'accounting', 'bank statements', 'invoices', 'invoice', 'tax', 'taxes',
                  'budget', 'balance sheet', 'payments', 'credit card', 'audit', 'bank details', 'payment details',
                  'accounts receivable', 'accounts payable', 'w-9', 'w9'],
    'Customer data': ['customer', 'customers', 'client', 'clients', 'crm', 'patients', 'students',
                      'members', 'policyholders', 'guests', 'tenants'],
    'Medical (PHI)': ['medical records', 'patient', 'patients', 'phi', 'health records', 'diagnosis',
                      'prescriptions', 'medical data', 'medical information', 'health information',
                      'medical files', 'medical reports'],
    'Legal / contracts': ['contract', 'contracts', 'agreement', 'agreements', 'nda', 'litigation', 'case files',
                          'legal documents', 'lawsuit', 'discovery', 'court files', 'court hearings',
                          'police reports'],
    'Intellectual property / technical': ['source code', 'blueprint', 'blueprints', 'drawings', 'cad', 'r&d',
                                          'research', 'patent', 'patents', 'formula', 'formulas', 'technical',
                                          'engineering', 'designs', 'product data', 'quality control',
                                          'certification', 'projects', 'project files', 'specifications'],
    'Credentials / IT': ['password', 'passwords', 'credentials', 'database', 'databases', 'backup', 'backups',
                         'sql', 'active directory', 'server'],
    'Corporate confidential': ['confidential', 'corporate', 'internal documents', 'board', 'correspondence',
                               'emails', 'email', 'mail', 'strategy', 'nda', 'counterparties', 'partners information',
                               'suppliers', 'business partners'],
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
    return classify_leak(record)

'''
company facts & leak details written into a post's text
some groups put labelled fields in the post body (incransom "Employees: 10 Revenue: $5 Million Industry: ...",
safepay "Revenue $5.8 Million") - these are site values. others only mention them in prose
("with 133 employees", "$7.3 million in revenue") - these are kept as text values so they can be filtered out.
only the description is read, the leak claim talks about stolen employee records, not headcount.
only dollar amounts are kept, no exchange rates are applied. a range keeps its lower bound
'''

MONEY = r'(?<![<≤])(?:us\s*)?\$\s?(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|mln|mn|bn|k|m|b)?\b'
MULTIPLIERS = {'thousand': 1e3, 'k': 1e3, 'million': 1e6, 'mln': 1e6, 'mn': 1e6, 'm': 1e6, 'billion': 1e9, 'bn': 1e9,
               'b': 1e9}
SITE_REVENUE_RE = re.compile(r'\brevenue\s*:?\s*' + MONEY, re.IGNORECASE)
TEXT_REVENUE_RES = [re.compile(r'\b(?:revenues?|turnover|annual sales)\b[^.$€£]{0,50}?' + MONEY, re.IGNORECASE),
                    re.compile(MONEY + r'\s+(?:in|of)\s+(?:annual\s+|yearly\s+)?(?:revenues?|turnover|sales)\b',
                               re.IGNORECASE)]
COUNT = r'(\d{1,3}(?:[,.]\d{3})+|\d+)'
SITE_EMPLOYEES_RE = re.compile(r'\bemployees\s*:\s*' + COUNT, re.IGNORECASE)
TEXT_EMPLOYEES_RES = [
    re.compile(r'\b(?:with|employs|employing|has|have|team of|approximately|around|about|over|more than|nearly|some)\s+'
               r'(?:(?:approximately|around|about|over|more than|nearly|some|a team of|a workforce of)\s+)?' + COUNT
               + r'\s*\+?\s*(?:[-–]\s*[\d,.]+\s*)?(?:full[- ]time\s+|permanent\s+)?(?:employees|staff members|staff)\b',
               re.IGNORECASE),
    re.compile(r'\b(?:employs|employing|employ)\s+(?:approximately|around|about|over|more than|nearly|some)?\s*' + COUNT
               + r'\s*\+?\s*(?:[-–]\s*[\d,.]+\s*)?(?:people|workers|employees|staff)\b', re.IGNORECASE),
    re.compile(r'\b(?:workforce|headcount)\s+of\s+(?:approximately|around|about|over|more than|nearly|some)?\s*' + COUNT,
               re.IGNORECASE),
]
INDUSTRY_LABEL_RE = re.compile(r'\bIndustry\s*:\s*(.+?)(?=\s+(?:Phone Number|Employees|Revenue)\b|$)')
SIZE_LABEL_RE = re.compile(r'\b(?:laek|leak size|data size|total data in the leak|total leak)\s*[:\-–]?\s*'
                           r'(\d+(?:[.,]\d+)?\s*(?:tb|gb|mb))\b', re.IGNORECASE)
# a windows dir listing pasted as proof: "Total Files Listed: 711367 File(s) 384,756,064,224 bytes"
FILE_LISTING_RE = re.compile(r'Total Files Listed:\s*([\d,]+)\s*File\(s\)\s*([\d,]+)\s*bytes', re.IGNORECASE)
# "DLs of more than 100 employees" counts stolen records, not staff
DATA_CONTEXT_RE = re.compile(r'\b(dls?|ssns?|scans?|records?|data|information|files?|docs?|documents?|forms?|details)\b'
                             r'[^.]{0,30}$', re.IGNORECASE)
CLAIM_HEADER_RE = re.compile(r'\b(?:we\s+)?ha(?:s|ve)\s+collected\s+such\s+data\b', re.IGNORECASE)
# post tags that name a type of stolen data
TAG_DATA_TYPES = {'AD%20Dump': 'Credentials / IT'}

def money(match):
    value = float(match.group(1).replace(',', ''))
    return int(value * MULTIPLIERS.get((match.group(2) or '').lower(), 1))

def count(text):
    # 1.200 is a european thousands separator, not a decimal
    return int(re.sub(r'[,.]', '', text))

def human_size(size):
    for unit, factor in (('TB', 1024 ** 4), ('GB', 1024 ** 3), ('MB', 1024 ** 2)):
        if size >= factor:
            return str(round(size / factor, 1)) + ' ' + unit
    return None

def first_match(patterns, text):
    for pattern in patterns:
        for match in pattern.finditer(text):
            if not DATA_CONTEXT_RE.search(text[max(0, match.start() - 60):match.start()]):
                return match
    return None

def site_facts(record):
    '''fills revenue, employees, industry, leak size & claim from the stored post text - safe to run repeatedly'''
    description = record.get('description') or ''
    # incransom appends its claim to the company blurb under a header the line splitter never saw
    header = CLAIM_HEADER_RE.search(description)
    if header and not record.get('leak_claim'):
        record['leak_claim'] = strip_noise(description[header.start():])
        record['description'] = description = clean(description[:header.start()]) or ''

    if record.get('revenue_usd') and not record.get('revenue_source'):
        record['revenue_source'] = 'site'
    if not record.get('revenue_usd'):
        match = SITE_REVENUE_RE.search(description)
        source = 'site'
        if not match:
            match, source = first_match(TEXT_REVENUE_RES, description), 'text'
        if match and money(match) >= 1000:
            record['revenue_usd'], record['revenue_source'] = money(match), source

    if not record.get('employees'):
        match = SITE_EMPLOYEES_RE.search(description)
        source = 'site'
        if not match:
            match, source = first_match(TEXT_EMPLOYEES_RES, description), 'text'
        if match and count(match.group(1)) > 0:
            record['employees'], record['employees_source'] = count(match.group(1)), source

    industry = INDUSTRY_LABEL_RE.search(description)
    if industry and not record.get('activity_raw'):
        record['activity_raw'] = clean(industry.group(1))
        activity = classify(record['activity_raw'], INDUSTRY_RES)
        if activity:
            record['activity'], record['activity_source'] = activity, 'site'

    listing = FILE_LISTING_RE.search(description)
    if listing:
        record['data_files'] = record.get('data_files') or count(listing.group(1))
        record['data_size'] = record.get('data_size') or human_size(count(listing.group(2)))
    size = SIZE_LABEL_RE.search(description)
    record['data_size'] = record.get('data_size') or (data_size_from(size.group(1)) if size else None) \
        or data_size_from(record.get('leak_claim'))

    if record.get('group') == 'incransom' and 'Encrypted' in (record.get('status') or '').split(','):
        record['encrypted'] = True
    record['revenue_band'] = band(record.get('revenue_usd'), REVENUE_BANDS)
    record['employee_band'] = band(record.get('employees'), EMPLOYEE_BANDS)
    return record

def classify_leak(record):
    '''data types & subject from the group's claim & data tags, else the leak's folder names - otherwise blank, not guessed'''
    types = data_types_from(record.get('leak_claim'))
    for tag in (record.get('status') or '').split(','):
        if tag in TAG_DATA_TYPES and TAG_DATA_TYPES[tag] not in types:
            types.append(TAG_DATA_TYPES[tag])
    source = 'claim' if types else None
    # the group's own words win, the leak's folder names only fill in where the group said nothing
    if not types and record.get('listing_types'):
        types, source = list(record['listing_types']), 'listing'
    record['data_types'] = ';'.join(types) or None
    record['data_types_source'] = source
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

def load_existing(jsonfile=JSONFILE):
    '''stored victims, minus any group no longer in groups.json - dropping a group there drops its rows'''
    if not os.path.exists(jsonfile):
        return {}
    tracked = {group['name'] for group in openjson('groups.json')}
    return {victimkey(r['group'], r['victim']): r for r in openjson(jsonfile) if r['group'] in tracked}

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

def merge(existing, found, cutoff=CUTOFF):
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
    for record in existing.values():
        site_facts(record)
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
        # a scheduled release date can lie in the future, but a post can't be dated later than the day
        # we first saw it
        record['date'] = min(record['published'], record['first_seen'][:10]) if record.get('published') \
            else record['first_seen'][:10]
        record['date_source'] = 'site' if record.get('published') else 'first_seen'
    # only attacks the group itself dated on or after the cutoff are kept - a first_seen date is when we
    # happened to scrape the post, which for an undated listing can be years after the attack
    kept = [r for r in existing.values() if r['date_source'] == 'site' and r['date'] >= cutoff]
    undated = sum(1 for r in existing.values() if r['date_source'] != 'site')
    stdlog('victims: kept ' + str(len(kept)) + ' victims dated ' + cutoff + ' or later, dropped '
           + str(len(existing) - len(kept) - undated) + ' older & ' + str(undated) + ' with no site date')
    return sorted(kept, key=lambda r: (r['date'], r['group'], r['victim']), reverse=True)

def write(records, jsonpath=JSONFILE, csvpath=CSVFILE, typespath=TYPESFILE):
    os.makedirs(DATADIR, exist_ok=True)
    with open(jsonpath, 'w', encoding='utf-8') as jsonfile:
        json.dump(records, jsonfile, ensure_ascii=False, indent=4)
    with open(csvpath, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    if typespath is None:
        return
    with open(typespath, 'w', encoding='utf-8', newline='') as csvfile:
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
        # some sites serve the first page again past the end of their archive
        seen = {v.get('post_url') or v.get('victim') for v in found}
        if not victims or all((v.get('post_url') or v.get('victim')) in seen for v in victims):
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
    # history first so a victim in both keeps the current table's record
    existing = load_existing(HISTJSON)
    existing.update(load_existing())
    found = []
    for group in openjson('groups.json'):
        if group['name'] not in PAGERS or (groups and group['name'] not in groups):
            continue
        victims = backfill_group(group, since, maxpages, workers)
        stdlog('victims: backfill ' + group['name'] + ' - ' + str(len(victims)) + ' victims since ' + since)
        found.extend(victims)
    # a backfill past the cutoff keeps the older posts in a separate history table
    records = merge(existing, found, cutoff=min(since, CUTOFF))
    current = [r for r in records if r['date'] >= CUTOFF]
    history = [r for r in records if r['date'] < CUTOFF]
    write(current)
    stdlog('victims: ' + str(len(current)) + ' victims written to ' + CSVFILE)
    if history or os.path.exists(HISTJSON):
        write(history, HISTJSON, HISTCSV, HISTTYPES)
        stdlog('victims: ' + str(len(history)) + ' older victims written to ' + HISTCSV)

'''
leak listings
safepay & qilin say nothing about what they took, but publish a browsable index of the stolen files.
only that index is read, never a file: archive links are skipped, non-html responses are dropped unread
and every response is capped. folder & file names often carry personal data (people's names, patients,
employees), so names only ever exist in memory - each is turned into a data type on the spot and only the
per-type counts are kept, in source/listing/ & on the record. no name is ever logged or written to disk
'''

LISTING_MAX_BYTES = 2 * 1024 * 1024
LISTING_MAX_SUBDIRS = 25
LISTING_GROUPS = ('safepay', 'qilin')
# qilin sends every request to a different file server mirror, often all down - try a few before spending
# a minute per post on the rest
LISTING_PROBE = 5
# folder & file name words the claim keywords don't cover - safepay victims are often german
FOLDER_TYPES = {
    'Employee / HR': ['lohn', 'gehalt', 'gehaelter', 'personalakte', 'personalakten', 'bewerbung', 'bewerbungen'],
    'Financial': ['buchhaltung', 'finanzen', 'rechnung', 'rechnungen', 'steuer', 'steuern', 'bank', 'datev',
                  'quickbooks', 'qbw', 'sage', 'ap', 'ar', 'billing'],
    'Customer data': ['kunden', 'kunde'],
    'Legal / contracts': ['vertrag', 'vertraege', 'verträge', 'legal', 'recht'],
    'Credentials / IT': ['bak', 'mdf', 'ldf', 'vmdk', 'vhdx', 'kdbx', 'it'],
    'Corporate confidential': ['pst', 'ost', 'management', 'geschaeftsfuehrung', 'geschäftsführung'],
}
FOLDER_TYPE_RES = {name: re.compile(r'\b(' + '|'.join(re.escape(k) for k in words) + r')\b')
                   for name, words in FOLDER_TYPES.items()}
ARCHIVE_RE = re.compile(r'\.(rar|zip|7z|tar|gz|tgz|bz2|xz|iso|exe|bin)$', re.IGNORECASE)
SKIP_LINK_RE = re.compile(r'^(\.\./?|/|\?.*|#.*|parent directory)$', re.IGNORECASE)

def listing_cache(group, url):
    return os.path.join(LISTINGDIR, group + '-' + hashlib.sha1(url.encode()).hexdigest()[:16] + '.json')

def listing_url(record):
    '''the leak index linked from a cached post page - none for archives or posts we have no page for'''
    cache = os.path.join(DETAILDIR, record['group'] + '-' + hashlib.sha1(record['post_url'].encode()).hexdigest()[:16]
                         + '.html')
    if not os.path.exists(cache):
        return None
    with open(cache, encoding='utf-8', errors='ignore') as cachefile:
        soup = BeautifulSoup(cachefile.read(), 'html.parser')
    if record['group'] == 'safepay':
        links = [a.get('href', '') for a in soup.select('a.btn-teal')]
        links = [link for link in links if '.onion/' in link and link.endswith('/')]
    else:
        links = [urljoin(record['post_url'], a.get('href', '')) for a in soup.select('a.learn_more')
                 if '/site/data' in a.get('href', '')]
    return links[0] if links else None

def fetch_capped(url):
    '''(index page text, url it was served from after redirects) - None for anything that is not a page,
    so a file served at the url is never read'''
    try:
        with requests.get(url, proxies=oproxies, headers=headers(), timeout=60, verify=False, stream=True) as response:
            kind = response.headers.get('Content-Type', '').lower()
            if response.status_code != 200 or not ('html' in kind or 'json' in kind):
                return None, None
            body = b''
            for chunk in response.iter_content(65536):
                body += chunk
                if len(body) >= LISTING_MAX_BYTES:
                    break
            return body.decode(response.encoding or 'utf-8', errors='ignore'), response.url
    except requests.exceptions.RequestException as error:
        errlog('victims: listing - ' + url + ' - ' + str(error))
        return None, None

def listing_entries(content, url):
    '''(names, subfolder urls) on an index page - autoindex style links, or name/path fields of a json index'''
    names, folders = [], []
    if content.lstrip()[:1] in '[{':
        try:
            stack = [json.loads(content)]
        except ValueError:
            stack = []
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                names.extend(str(v) for k, v in item.items() if k.lower() in ('name', 'path', 'filename', 'title')
                             and isinstance(v, str))
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
        return names, folders
    host = urlsplit(url).netloc
    for link in BeautifulSoup(content, 'html.parser').find_all('a'):
        href, text = link.get('href', ''), clean(link.get_text()) or ''
        if not href or SKIP_LINK_RE.match(href) or SKIP_LINK_RE.match(text):
            continue
        target = urljoin(url, href)
        if urlsplit(target).netloc != host or target == url:
            continue
        # an autoindex link extends the index url, other indexes (qilin) are read by link text
        if target.startswith(url):
            names.append(unquote(target[len(url):].rstrip('/')))
            if target.endswith('/') and not ARCHIVE_RE.search(target.rstrip('/')):
                folders.append(target)
        elif text:
            names.append(text)
    return names, folders

def name_types(name):
    '''data types a folder or file name points at - "HR_Payroll/Lohn2024.xlsx" -> employee & financial'''
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    text = re.sub(r'[_\-.\\/()\[\]]+|(?<=[a-z])(?=\d)', ' ', text).lower()
    # in a file name "dl" is a download, not a driving licence
    text = re.sub(r'\bdl\b', ' ', text)
    types = data_types_from(text)
    return types + [t for t, pattern in FOLDER_TYPE_RES.items() if pattern.search(text) and t not in types]

def read_listing(url):
    '''{data type: number of names pointing at it} & how many names were read, over the index & its subfolders'''
    counts, entries = {}, 0
    # qilin's data page redirects to the index on a separate file server
    content, url = fetch_capped(url)
    if content is None:
        return None
    names, folders = listing_entries(content, url)
    for folder in folders[:LISTING_MAX_SUBDIRS]:
        page, served = fetch_capped(folder)
        if page:
            names.extend(folder[len(url):] + name for name in listing_entries(page, served)[0])
    for name in names:
        entries += 1
        for data_type in name_types(name):
            counts[data_type] = counts.get(data_type, 0) + 1
    return {'entries': entries, 'types': counts, 'fetched': now()}

def apply_listing(record):
    '''put a cached index summary on the record - no fetching'''
    if record['group'] not in LISTING_GROUPS or not record.get('post_url'):
        return
    url = listing_url(record)
    cache = listing_cache(record['group'], url) if url else None
    if cache is None or not os.path.exists(cache):
        return
    summary = openjson(cache)
    record['listing_entries'] = summary['entries']
    # most-named first, a type named once in thousands of files is noise rather than what the leak holds
    record['listing_types'] = [t for t, n in sorted(summary['types'].items(), key=lambda kv: -kv[1]) if n >= 2]

def listings(groups=None, workers=4):
    '''read the leak index of every published safepay / qilin post we have a page for, then rebuild the tables'''
    records = list(load_existing(HISTJSON).values()) + list(load_existing().values())
    todo = {}
    for record in records:
        if record['group'] not in LISTING_GROUPS or (groups and record['group'] not in groups):
            continue
        if record.get('status') == 'pending' or not record.get('post_url'):
            continue
        url = listing_url(record)
        if url and not os.path.exists(listing_cache(record['group'], url)):
            todo[url] = record['group']
    stdlog('victims: listings - ' + str(len(todo)) + ' leak indexes to read')
    os.makedirs(LISTINGDIR, exist_ok=True)
    def work(item):
        url, group = item
        summary = read_listing(url)
        # failures aren't cached, so the next run tries them again
        if summary is not None:
            with open(listing_cache(group, url), 'w', encoding='utf-8') as cachefile:
                json.dump(summary, cachefile)
        return summary is not None
    done = 0
    for group in LISTING_GROUPS:
        items = [(url, g) for url, g in todo.items() if g == group]
        if not items:
            continue
        probe = [work(item) for item in items[:LISTING_PROBE]]
        if not any(probe):
            errlog('victims: listings - ' + group + ' - first ' + str(len(probe)) + ' indexes unreachable, skipping '
                   + str(len(items) - len(probe)) + ' more')
            continue
        with ThreadPoolExecutor(max_workers=workers) as pool:
            done += sum(probe) + sum(pool.map(work, items[LISTING_PROBE:]))
    stdlog('victims: listings - read ' + str(done) + ' of ' + str(len(todo)))
    main()

'''
proof hashes
a group's proof ids are upload ids, so the same screenshot posted again gets a new id. to spot re-posted
proofs each proof image is fetched & hashed - sha256 for the identical file, a 64 bit difference hash for
the same picture re-saved or resized. proofs are usually photos of the stolen documents, so an image only
ever exists in memory: it is hashed & dropped, never written to disk, and only the hashes are kept
(source/proofhash/<group>.json, proof id -> [sha256, dhash]). qilin is read from its thumbnails
'''

PROOF_MAX_BYTES = 10 * 1024 * 1024
PROOF_GROUPS = ('qilin', 'incransom')
QILIN_THUMB_RE = re.compile(r'src="(/uploads/blog/\d+/photos/thumbs/([0-9a-f]{32})\.\w+)"')
# a hash shared by more posts than this is a group banner or template, not a re-post
PROOF_TEMPLATE_MIN = 5
BLANK_DHASHES = {'0000000000000000', 'ffffffffffffffff'}

def proof_urls(record):
    '''proof id -> image url for a post'''
    ids = [p for p in (record.get('proof_ids') or '').split(';') if p]
    if not ids:
        return {}
    if record['group'] == 'incransom':
        # the blog renders each proof from {api}/api/v1/blog/download/{id}
        return {p: base_url(record['source_url']) + '/api/v1/blog/download/' + p for p in ids}
    cache = os.path.join(DETAILDIR, 'qilin-' + hashlib.sha1((record.get('post_url') or '').encode()).hexdigest()[:16]
                         + '.html')
    if not os.path.exists(cache):
        return {}
    with open(cache, encoding='utf-8', errors='ignore') as cachefile:
        thumbs = {digest: path for path, digest in QILIN_THUMB_RE.findall(cachefile.read())}
    return {p: base_url(record['post_url']) + thumbs[p] for p in ids if p in thumbs}

def dhash(image):
    '''64 bit difference hash - neighbouring pixel brightness on a 9x8 greyscale copy'''
    pixels = list(image.convert('L').resize((9, 8), Image.LANCZOS).tobytes())
    bits = ''.join('1' if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else '0'
                   for row in range(8) for col in range(8))
    return '%016x' % int(bits, 2)

def hash_proof(url):
    '''[sha256, dhash] of the image at url, or None - the image itself is never kept'''
    try:
        with requests.get(url, proxies=oproxies, headers=headers(), timeout=90, verify=False, stream=True) as response:
            if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('image/'):
                return None
            body = b''
            for chunk in response.iter_content(65536):
                body += chunk
                if len(body) > PROOF_MAX_BYTES:
                    return None
        with Image.open(io.BytesIO(body)) as image:
            return [hashlib.sha256(body).hexdigest(), dhash(image)]
    except (requests.exceptions.RequestException, OSError, Image.DecompressionBombError) as error:
        errlog('victims: proof - ' + url + ' - ' + type(error).__name__)
        return None

def proof_hash_cache(group):
    path = os.path.join(PROOFHASHDIR, group + '.json')
    return openjson(path) if os.path.exists(path) else {}

def apply_proof_hashes(records):
    caches = {group: proof_hash_cache(group) for group in PROOF_GROUPS}
    for record in records:
        hashes = [caches[record['group']][p] for p in (record.get('proof_ids') or '').split(';')
                  if record['group'] in caches and p in caches[record['group']]]
        record['proof_sha256'] = ';'.join(h[0] for h in hashes) or None
        record['proof_dhash'] = ';'.join(h[1] for h in hashes) or None

def proofs(groups=None, workers=6):
    '''hash every proof image not hashed yet, then rebuild the tables'''
    records = list(load_existing(HISTJSON).values()) + list(load_existing().values())
    os.makedirs(PROOFHASHDIR, exist_ok=True)
    for group in PROOF_GROUPS:
        if groups and group not in groups:
            continue
        cache = proof_hash_cache(group)
        todo = {}
        for record in records:
            if record['group'] == group:
                todo.update({p: u for p, u in proof_urls(record).items() if p not in cache})
        stdlog('victims: proofs - ' + group + ' - ' + str(len(todo)) + ' images to hash, ' + str(len(cache)) + ' done')
        items = list(todo.items())
        # saved every batch so an interrupted run keeps its progress
        for start in range(0, len(items), 200):
            batch = items[start:start + 200]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for (proof, _), hashes in zip(batch, pool.map(lambda item: hash_proof(item[1]), batch)):
                    if hashes:
                        cache[proof] = hashes
            with open(os.path.join(PROOFHASHDIR, group + '.json'), 'w', encoding='utf-8') as cachefile:
                json.dump(cache, cachefile)
            stdlog('victims: proofs - ' + group + ' - ' + str(min(start + 200, len(items))) + '/' + str(len(items)))
    main()

PROOF_DISTANCE_MAX = 4
POPCOUNT = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint8)

def bit_distance(left, right):
    '''bits that differ between every hash in left & every hash in right - a len(left) x len(right) matrix'''
    xor = np.ascontiguousarray(left[:, None] ^ right[None, :])
    return POPCOUNT[xor.view(np.uint8)].reshape(xor.shape + (8,)).sum(axis=-1)

def write_proof_distances(study, records):
    '''
    one row per pair of a study post (the 2026 table) & any other post whose proof images come within
    PROOF_DISTANCE_MAX bits of each other. distance is the number of differing bits between two 64 bit
    difference hashes - 0 is the same picture, re-saved or resized pictures stay within a few bits
    '''
    posts = [r for r in records if r.get('proof_dhash')]
    hashes, owners = [], []
    for index, record in enumerate(posts):
        for value in record['proof_dhash'].split(';'):
            if value not in BLANK_DHASHES:
                hashes.append(int(value, 16))
                owners.append(index)
    hashes, owners = np.array(hashes, dtype=np.uint64), np.array(owners)
    study_keys = {victimkey(r['group'], r['victim']) for r in study}
    rows = []
    for index, record in enumerate(posts):
        if victimkey(record['group'], record['victim']) not in study_keys:
            continue
        mine = owners == index
        if not mine.any():
            continue
        distance = bit_distance(hashes[mine], hashes[~mine])
        others = owners[~mine]
        close = distance <= PROOF_DISTANCE_MAX
        # an image close to many posts is a banner or a blank-ish page, not a re-post
        for row in range(close.shape[0]):
            if len(set(others[close[row]])) > PROOF_TEMPLATE_MIN:
                close[row] = False
        sha = set((record.get('proof_sha256') or '').split(';')) - {''}
        for other in sorted(set(others[close.any(axis=0)])):
            columns = others == other
            best = distance[:, columns].min(axis=1)
            matched = posts[other]
            rows.append({
                'group': record['group'], 'victim': record['victim'], 'date': record['date'],
                'other_group': matched['group'], 'other_victim': matched['victim'], 'other_date': matched['date'],
                'gap_days': (datetime.strptime(matched['date'], '%Y-%m-%d')
                             - datetime.strptime(record['date'], '%Y-%m-%d')).days,
                'min_distance': int(best.min()),
                'mean_distance': round(float(best.mean()), 1),
                'close_images': int(close[:, columns].any(axis=1).sum()),
                'proof_count': int(mine.sum()),
                'other_proof_count': int(columns.sum()),
                'identical': len(sha & set((matched.get('proof_sha256') or '').split(';'))),
            })
            rows[-1]['overlap'] = round(rows[-1]['close_images'] / rows[-1]['proof_count'], 2)
    rows.sort(key=lambda r: (-r['overlap'], r['min_distance'], r['mean_distance']))
    columns = ['group', 'victim', 'date', 'other_group', 'other_victim', 'other_date', 'gap_days', 'min_distance',
               'mean_distance', 'close_images', 'proof_count', 'other_proof_count', 'overlap', 'identical']
    with open(PROOFDISTANCES, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    stdlog('victims: ' + str(len(rows)) + ' post pairs within ' + str(PROOF_DISTANCE_MAX) + ' bits written to '
           + PROOFDISTANCES)

def refresh_details(records):
    '''re-read cached post pages so fields added to a detail parser reach posts fetched before it - never fetches'''
    for record in records:
        parser = DETAIL_PARSERS.get(record['group'])
        if parser is None or not record.get('post_url'):
            continue
        cache = os.path.join(DETAILDIR, record['group'] + '-' + hashlib.sha1(record['post_url'].encode()).hexdigest()[:16]
                             + '.html')
        if not os.path.exists(cache) or os.path.getsize(cache) == 0:
            continue
        with open(cache, encoding='utf-8', errors='ignore') as cachefile:
            fields = parser(cachefile.read())
        record.update({k: v for k, v in fields.items() if v not in (None, '') and record.get(k) in (None, '')})
        apply_listing(record)

def main():
    stdlog('victims: building structured victim table')
    existing = load_existing()
    refresh_details(existing.values())
    apply_proof_hashes(existing.values())
    records = merge(existing, collect())
    write(records)
    stdlog('victims: ' + str(len(records)) + ' victims written to ' + CSVFILE)
    history = []
    if os.path.exists(HISTJSON):
        history = load_existing(HISTJSON)
        refresh_details(history.values())
        apply_proof_hashes(history.values())
        history = [r for r in merge(history, [], cutoff='0000-00-00') if r['date'] < CUTOFF]
        write(history, HISTJSON, HISTCSV, HISTTYPES)
        stdlog('victims: ' + str(len(history)) + ' older victims written to ' + HISTCSV)
    # the study period is the 2026 table, matched against every post we hold
    write_proof_distances(records, records + history)

if __name__ == '__main__':
    main()
