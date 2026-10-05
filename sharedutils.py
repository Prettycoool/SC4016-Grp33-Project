#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
collection of shared modules used throughout ransomwatch
'''
import os
import sys
import json
import time
import socket
import random
import logging
from datetime import datetime
from datetime import timedelta
import subprocess
import tldextract
import lxml.html
import requests
import tweepy

sockshost = '127.0.0.1'
socksport = 9050

logging.basicConfig(
    format='%(asctime)s,%(msecs)d %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d:%H:%M:%S',
    level=logging.INFO
    )

def stdlog(msg):
    '''standard infologging'''
    logging.info(msg)

def dbglog(msg):
    '''standard debug logging'''
    logging.debug(msg)

def errlog(msg):
    '''standard error logging'''
    logging.error(msg)

def honk(msg):
    '''critical error logging with termination'''
    logging.critical(msg)
    sys.exit()

def currentmonthstr():
    '''
    return the current, full month name in lowercase
    '''
    return datetime.now().strftime('%B').lower()

# socks5h:// ensures we route dns requests through the socks proxy
# reduces the risk of dns leaks & allows us to resolve hidden services

oproxies = {
    'http':  'socks5h://' + str(sockshost) + ':' + str(socksport),
    'https': 'socks5h://' + str(sockshost) + ':' + str(socksport)
}

def checkgeckodriver():
    '''
    check if geckodriver is in the system $PATH
    '''
    dbglog('sharedutils: ' + 'checking if geckodriver is in $PATH')
    cmd = 'geckodriver --version'
    try:
        output = runshellcmd(cmd)
        if 'geckodriver' in output[0]:
            dbglog('sharedutils: ' + 'geckodriver is in $PATH')
            return True
        errlog('sharedutils: ' + 'geckodriver is not in $PATH')
        return False
    except subprocess.CalledProcessError as cpe:
        errlog('sharedutils: ' + 'geckodriver check failed - ' + str(cpe))
        return False

def randomagent():
    '''
    randomly return a useragent from assets/useragents.txt
    '''
    with open('assets/useragents.txt', encoding='utf-8') as uafile:
        uas = uafile.read().splitlines()
        uagt = random.choice(uas)
        dbglog('sharedutils: ' + 'random user agent - ' + str(uagt))
    return uagt

def headers():
    '''
    returns a key:val user agent header for use with the requests library
    '''
    headerstr = {'User-Agent': str(randomagent())}
    return headerstr

def socksfetcher(url):
    '''
    fetch a url via socks proxy
    '''
    try:
        stdlog('sharedutils: ' + 'starting socks request to ' + str(url))
        start_time = time.time()
        request = requests.get(url, proxies=oproxies, headers=headers(), timeout=35, verify=False)
        end_time = time.time()
        elapsed_time = end_time - start_time
        stdlog('sharedutils: ' + f'socks request to {url} completed in {elapsed_time:.2f} seconds')
        dbglog(
            'sharedutils: ' + 'socks request - recieved statuscode - ' \
                + str(request.status_code)
            )
        try:
            response = request.text
            return response
        except AttributeError as ae:
            errlog('sharedutils: ' + 'socks response error - ' + str(ae))
            return None
    except requests.exceptions.Timeout:
        errlog('sharedutils: ' + 'socks request timed out!')
        return None
    except requests.exceptions.ConnectionError as rec:
        # catch SOCKSHTTPConnectionPool Host unreachable
        if 'SOCKSHTTPConnectionPool' and 'Host unreachable' in str(rec):
            errlog('sharedutils: ' + 'socks request unable to route to host, check hsdir resolution status!')
            return None
        errlog('sharedutils: ' + 'socks request connection error - ' + str(rec))
        return None
    except requests.exceptions.TooManyRedirects as ret:
        errlog('sharedutils: ' + 'socks request too many redirects - ' + str(ret))
        return None

def siteschema(location):
    '''
    returns a dict with the site schema
    '''
    if not location.startswith('http'):
        dbglog('sharedutils: ' + 'assuming we have been given an fqdn and appending protocol')
        location = 'http://' + location
    schema = {
        'fqdn': getapex(location),
        'title': None,
        'version': getonionversion(location)[0],
        'slug': location,
        'available': False,
        'updated': None,
        'lastscrape': '2021-05-01 00:00:00.000000',
        'enabled': True
    }
    dbglog('sharedutils: ' + 'schema - ' + str(schema))
    return schema

def runshellcmd(cmd):
    '''
    runs a shell command and returns the output
    parser pipelines commonly end in grep/awk, which exit non-zero on no-match
    (e.g. when the group's source html doesn't exist) - that's a normal
    "no posts found" outcome for a parser, not a fatal error, so it's
    treated the same as an empty-but-successful pipeline rather than raised
    '''
    stdlog('sharedutils: ' + 'running shell command - ' + str(cmd))
    try:
        cmdout = subprocess.run(
            cmd,
            shell=True,
            universal_newlines=True,
            check=True,
            stdout=subprocess.PIPE
            )
    except subprocess.CalledProcessError as cpe:
        dbglog('sharedutils: ' + 'shell command returned non-zero exit status - ' + str(cpe))
        return ['']
    response = cmdout.stdout.strip().split('\n')
    return response

def getsitetitle(html) -> str:
    '''
    tried to parse out the title of a site from the html
    '''
    stdlog('sharedutils: ' + 'getting site title')
    try:
        title = lxml.html.parse(html)
        titletext = title.find(".//title").text
    except AssertionError:
        stdlog('sharedutils: ' + 'could not fetch site title from source - ' + str(html))
        return None
    except AttributeError:
        stdlog('sharedutils: ' + 'could not fetch site title from source - ' + str(html))
        return None
    # limit title text to 50 chars
    if titletext is not None:
        if len(titletext) > 50:
            titletext = titletext[:50]
        stdlog('sharedutils: ' + 'site title - ' + str(titletext))
        return titletext
    stdlog('sharedutils: ' + 'could not find site title from source - ' + str(html))
    return None

def gcount(posts):
    group_counts = {}
    for post in posts:
        if post['group_name'] in group_counts:
            group_counts[post['group_name']] += 1
        else:
            group_counts[post['group_name']] = 1
    return group_counts

def getapex(slug):
    '''
    returns the domain for a given webpage/url slug
    '''
    stripurl = tldextract.extract(slug)
    if stripurl.subdomain:
        return stripurl.subdomain + '.' + stripurl.domain + '.' + stripurl.suffix
    return stripurl.domain + '.' + stripurl.suffix

def striptld(slug):
    '''
    strips the tld from a url
    '''
    stripurl = tldextract.extract(slug)
    return stripurl.domain

def getonionversion(slug):
    '''
    returns the version of an onion service (v2/v3)
    https://support.torproject.org/onionservices/v2-deprecation
    '''
    version = None
    stripurl = tldextract.extract(slug)
    location = stripurl.domain + '.' + stripurl.suffix
    stdlog('sharedutils: ' + 'checking for onion version - ' + str(location))
    if len(stripurl.domain) == 16:
        stdlog('sharedutils: ' + 'v2 onionsite detected')
        version = 2
    elif len(stripurl.domain) == 56:
        stdlog('sharedutils: ' + 'v3 onionsite detected')
        version = 3
    else:
        stdlog('sharedutils: ' + 'unknown onion version, assuming clearnet')
        version = 0
    return version, location

def openjson(file):
    '''
    opens a file and returns the json as a dict
    '''
    with open(file, encoding='utf-8') as jsonfile:
        data = json.load(jsonfile)
    return data

def writejson(file, data, **kwargs):
    '''
    writes data to a file as json - utf-8 kept as is & indented unless told otherwise
    '''
    with open(file, 'w', encoding='utf-8') as jsonfile:
        json.dump(data, jsonfile, **{'ensure_ascii': False, 'indent': 4, **kwargs})

def checktcp(host, port):
    '''
    checks if a tcp port is open - used to check if a socks proxy is available
    '''
    dbglog('sharedutils: ' + 'attempting socket connection')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((str(host), int(port)))
    sock.close()
    if result == 0:
        dbglog('sharedutils: ' + 'socket connected to ' + str(host) + ':' + str(port))
        return True
    stdlog('sharedutils: ' + 'socket failed connection to ' + str(host) + ':' + str(port))
    return False

def requiresocks(prefix=''):
    '''
    exits if the socks proxy needed to reach onionsites is down
    '''
    if not checktcp(sockshost, socksport):
        honk(prefix + 'socks proxy unavailable and required to fetch onionsites!')

def postcount():
    # counts from 1, as it always has - the published totals carry that offset
    return len(openjson('posts.json')) + 1

def groupcount():
    groups = openjson('groups.json')
    return len(groups)

def parsercount():
    # counts from 1, as it always has
    return 1 + sum(1 for group in openjson('groups.json') if group['parser'] is True)

def hostcount(online_only=False):
    '''
    number of locations across all groups, or only those up at the last scrape
    '''
    return sum(1 for group in openjson('groups.json') for host in group['locations']
               if host['available'] is True or not online_only)

def countposts(within):
    '''
    number of posts whose discovered time passes the within check
    '''
    return sum(1 for post in openjson('posts.json')
               if within(datetime.strptime(post['discovered'], '%Y-%m-%d %H:%M:%S.%f')))

def monthlypostcount():
    '''returns the number of posts within the current month'''
    now = datetime.now()
    return countposts(lambda found: found.year == now.year and found.month == now.month)

def postssince(days):
    '''returns the number of posts within the last x days'''
    return countposts(lambda found: found > datetime.now() - timedelta(days=days))

def poststhisyear():
    '''returns the number of posts within the current year'''
    return countposts(lambda found: found.year == datetime.now().year)

def todiscord(post_title, group, hook_uri):
    '''
    sends a post to a discord webhook defined as an envar
    '''
    dbglog('sharedutils: ' + 'sending to discord webhook')
    # avoid json decode errors by escaping the title if contains \ or "
    post_title = post_title.replace('\\', '\\\\').replace('"', '\\"')
    discord_data = '''
    {
        "content": "[**%s**](https://ransomwatch.telemetry.ltd/#/profiles?id=%s) posted `%s`",
        "embeds": null,
        "username": "ransomwatch",
        "avatar_url": "https://github.com/joshhighet/ransomwatch/blob/main/docs/apple-touch-icon.png?raw=true",
        "attachments": [],
        "flags": 4
    }''' % (group, group, post_title)
    discord_json = json.loads(discord_data)
    stdlog('sharedutils: ' + 'sending to discord webhook')
    dscheaders = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    try:
        hookpost = requests.post(hook_uri, json=discord_json, headers=dscheaders)
    except requests.exceptions.RequestException as e:
        honk('sharedutils: ' + 'error sending to discord webhook: ' + str(e))
    if hookpost.status_code == 204:
        return True
    if hookpost.status_code == 429:
        errlog('sharedutils: ' + 'discord webhook rate limit exceeded')
    else:
        honk('sharedutils: ' + 'recieved discord webhook error resonse ' + str(hookpost.status_code) + ' with text ' + str(hookpost.text))
    return False

def totweet(post_title, group):
    stdlog('sharedutils: ' + 'posting to x')
    X_CONSUMER_KEY = str(os.environ.get('X_CONSUMER_KEY'))
    X_CONSUMER_SECRET = str(os.environ.get('X_CONSUMER_SECRET'))
    X_ACCESS_TOKEN = str(os.environ.get('X_ACCESS_TOKEN'))
    X_ACCESS_TOKEN_SECRET = str(os.environ.get('X_ACCESS_TOKEN_SECRET'))
    try:
        client = tweepy.Client(
            consumer_key=X_CONSUMER_KEY,
            consumer_secret=X_CONSUMER_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET
            )
        timestamp = datetime.now().strftime('%H:%M %d/%m/%y')
        status = f"Group: {group}\nApprox. Time: {timestamp}\nTitle: {post_title}"
        client.create_tweet(text=status)
    except TypeError as te:
        errlog('x unsatisfied: ' + str(te))
    except tweepy.errors.TooManyRequests as tmr:
        errlog('x rate limit exceeded: ' + str(tmr))
    except Exception as e:
        errlog('x unhandled error: ' + str(e))
