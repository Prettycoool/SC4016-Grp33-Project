#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
company size & revenue lookup against wikidata
reads data/victims.json & writes data/company_size.csv - one row per victim, matched or not,
so coverage is visible. every victim is tried two ways, in order of confidence:
  website - the victim's domain equals a wikidata item's official website (P856)
  name    - the victim's name is exactly the english label/alias of a single wikidata item that is a business
wikidata mostly knows larger & listed companies, so matches skew big - keep that in mind when reading the output
this is a clearnet lookup, not routed over tor
'''
import os
import re
import csv
import sys
import json
import time
from datetime import datetime

import requests

DATADIR = 'data'
VICTIMS = os.path.join(DATADIR, 'victims.json')
OUTFILE = os.path.join(DATADIR, 'company_size.csv')

SPARQL = 'https://query.wikidata.org/sparql'
API = 'https://www.wikidata.org/w/api.php'
# wikimedia asks for a descriptive agent with a contact point
AGENT = 'SC4016-ransomwatch-research/1.0 (https://github.com/Prettycoool/SC4016-Grp-Project) python-requests'
BATCH = 40
PAUSE = 1.0

COLUMNS = [
    'group', 'victim', 'website', 'date', 'match_method', 'wikidata_id', 'wikidata_label',
    'employees', 'employees_year', 'revenue', 'revenue_currency', 'revenue_year', 'revenue_usd',
    'wikidata_country', 'wikidata_industry',
]

session = requests.Session()
session.headers.update({'User-Agent': AGENT})

def log(msg):
    print(datetime.now().strftime('%H:%M:%S') + ' companysize: ' + msg, flush=True)

def get(url, params, retries=5):
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=90)
            if response.status_code == 200:
                return response.json()
            # 429 / 5xx - back off, honouring retry-after when given
            wait = int(response.headers.get('Retry-After', 0) or 0) or 5 * (attempt + 1)
            log('http ' + str(response.status_code) + ', waiting ' + str(wait) + 's')
            time.sleep(wait)
        except (requests.exceptions.RequestException, ValueError) as error:
            log('request failed - ' + str(error))
            time.sleep(5 * (attempt + 1))
    return None

def sparql(query):
    time.sleep(PAUSE)
    data = get(SPARQL, {'query': query, 'format': 'json'})
    return data['results']['bindings'] if data else []

def value(row, key):
    return row[key]['value'] if key in row else None

def qid(uri):
    return uri.rsplit('/', 1)[-1] if uri else None

def year(stamp):
    return int(stamp[:4]) if stamp and stamp[:4].isdigit() else None

def site_variants(domain):
    '''wikidata stores official websites as full urls, in whatever form the editor typed'''
    for scheme in ('https://', 'http://'):
        for host in (domain, 'www.' + domain):
            for tail in ('', '/'):
                yield scheme + host + tail

# the facts pulled for each matched item - latest statement wins, by its point in time qualifier
FACTS = '''
  OPTIONAL { ?item p:P1128 ?es . ?es ps:P1128 ?emp . OPTIONAL { ?es pq:P585 ?empDate } }
  OPTIONAL { ?item p:P2139 ?rs . ?rs psv:P2139 ?rv . ?rv wikibase:quantityAmount ?rev ; wikibase:quantityUnit ?revUnit .
             OPTIONAL { ?revUnit wdt:P498 ?revCode } OPTIONAL { ?rs pq:P585 ?revDate } }
  OPTIONAL { ?item wdt:P17 ?country . ?country wdt:P297 ?countryCode }
  OPTIONAL { ?item wdt:P452 ?industry . ?industry rdfs:label ?industryLabel . FILTER(LANG(?industryLabel) = "en") }
  OPTIONAL { ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "en") }
'''
SELECT = 'SELECT ?item ?itemLabel ?emp ?empDate ?rev ?revCode ?revDate ?countryCode ?industryLabel'

def collect_facts(rows):
    '''sparql rows (one per statement combination) -> {qid: facts}'''
    items = {}
    for row in rows:
        item = qid(value(row, 'item'))
        facts = items.setdefault(item, {'label': value(row, 'itemLabel'), 'emp': {}, 'rev': {},
                                        'country': value(row, 'countryCode'), 'industry': set()})
        if value(row, 'emp'):
            facts['emp'][year(value(row, 'empDate')) or 0] = float(value(row, 'emp'))
        if value(row, 'rev'):
            facts['rev'][year(value(row, 'revDate')) or 0] = (float(value(row, 'rev')), value(row, 'revCode'))
        if value(row, 'industryLabel'):
            facts['industry'].add(value(row, 'industryLabel'))
        if value(row, 'site'):
            facts['site'] = value(row, 'site')
    return items

def by_website(domains):
    '''{domain: qid}, {qid: facts} for domains that are exactly one item's official website'''
    matches, facts = {}, {}
    domains = sorted(domains)
    for start in range(0, len(domains), BATCH):
        batch = domains[start:start + BATCH]
        sites = ' '.join('<' + site + '>' for domain in batch for site in site_variants(domain))
        rows = sparql(SELECT + ' ?site WHERE { VALUES ?site { ' + sites + ' } ?item wdt:P856 ?site . ' + FACTS + '}')
        found = collect_facts(rows)
        owners = {}
        for item, item_facts in found.items():
            host = re.sub(r'^https?://(www\.)?', '', item_facts.get('site', '')).rstrip('/')
            owners.setdefault(host, set()).add(item)
        for host, items in owners.items():
            # a domain claimed by several items (a company & its brands, say) is ambiguous - skip it
            if len(items) == 1:
                matches[host] = next(iter(items))
        facts.update(found)
        log('website lookup ' + str(min(start + BATCH, len(domains))) + '/' + str(len(domains))
            + ' - ' + str(len(matches)) + ' matched so far')
    return matches, facts

def normalise(name):
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())

def by_name(names):
    '''{name: qid} for names that exactly equal the label or alias of exactly one business item'''
    candidates = {}
    for count, name in enumerate(sorted(names), 1):
        time.sleep(PAUSE)
        data = get(API, {'action': 'wbsearchentities', 'search': name, 'language': 'en', 'type': 'item',
                         'limit': 10, 'format': 'json'}) or {}
        exact = {hit['id'] for hit in data.get('search', [])
                 if normalise(hit.get('match', {}).get('text')) == normalise(name)}
        if exact:
            candidates[name] = exact
        if count % 50 == 0:
            log('name search ' + str(count) + '/' + str(len(names)) + ' - ' + str(len(candidates)) + ' with exact hits')
    # keep only candidates that are a business / organisation, then only names left with a single item
    ids = sorted({item for items in candidates.values() for item in items})
    businesses, facts = set(), {}
    for start in range(0, len(ids), BATCH):
        batch = ' '.join('wd:' + item for item in ids[start:start + BATCH])
        rows = sparql(SELECT + ' WHERE { VALUES ?item { ' + batch + ' } '
                      '?item wdt:P31/wdt:P279* ?kind . VALUES ?kind { wd:Q4830453 wd:Q43229 } ' + FACTS + '}')
        found = collect_facts(rows)
        businesses.update(found)
        facts.update(found)
    matches = {}
    for name, items in candidates.items():
        items = items & businesses
        if len(items) == 1:
            matches[name] = next(iter(items))
    log('name lookup - ' + str(len(matches)) + ' of ' + str(len(names)) + ' names matched a single business')
    return matches, facts

def latest(series):
    '''value from the most recent dated statement, falling back to an undated one'''
    if not series:
        return None, None
    stamp = max(series)
    return series[stamp], stamp or None

def row_for(victim, method, item, facts):
    row = {'group': victim['group'], 'victim': victim['victim'], 'website': victim.get('website'),
           'date': victim.get('date'), 'match_method': method}
    if not item:
        return row
    fact = facts.get(item, {})
    employees, employees_year = latest(fact.get('emp'))
    revenue, revenue_year = latest(fact.get('rev'))
    amount, currency = revenue if revenue else (None, None)
    row.update({
        'wikidata_id': item,
        'wikidata_label': fact.get('label'),
        'employees': int(employees) if employees is not None else None,
        'employees_year': employees_year,
        'revenue': int(amount) if amount is not None else None,
        'revenue_currency': currency,
        'revenue_year': revenue_year,
        # only same-currency values are carried over - no exchange rates are guessed
        'revenue_usd': int(amount) if amount is not None and currency == 'USD' else None,
        'wikidata_country': fact.get('country'),
        'wikidata_industry': ';'.join(sorted(fact.get('industry', []))) or None,
    })
    return row

def main(since=None):
    with open(VICTIMS, encoding='utf-8') as victimsfile:
        victims = json.load(victimsfile)
    if since:
        victims = [v for v in victims if (v.get('date') or '') >= since]
    log(str(len(victims)) + ' victims to look up' + (' since ' + since if since else ''))

    domains = {v['website'].lower() for v in victims if v.get('website')}
    site_matches, facts = by_website(domains)
    unmatched = {v['victim'] for v in victims if not site_matches.get((v.get('website') or '').lower())}
    name_matches, name_facts = by_name(unmatched)
    facts.update(name_facts)

    rows = []
    for victim in victims:
        item = site_matches.get((victim.get('website') or '').lower())
        if item:
            rows.append(row_for(victim, 'website', item, facts))
        elif victim['victim'] in name_matches:
            rows.append(row_for(victim, 'name', name_matches[victim['victim']], facts))
        else:
            rows.append(row_for(victim, None, None, facts))

    with open(OUTFILE, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    matched = [r for r in rows if r.get('wikidata_id')]
    log(str(len(matched)) + '/' + str(len(rows)) + ' victims matched ('
        + str(sum(1 for r in matched if r['match_method'] == 'website')) + ' by website, '
        + str(sum(1 for r in matched if r['match_method'] == 'name')) + ' by name), '
        + str(sum(1 for r in matched if r.get('employees'))) + ' with employees, '
        + str(sum(1 for r in matched if r.get('revenue'))) + ' with revenue - written to ' + OUTFILE)

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
