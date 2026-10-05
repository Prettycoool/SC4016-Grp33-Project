#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import urllib.parse
from datetime import datetime as dt

from sharedutils import openjson
from sharedutils import postcount
from sharedutils import hostcount
from sharedutils import groupcount
from sharedutils import postssince
from sharedutils import parsercount
from sharedutils import poststhisyear
from sharedutils import currentmonthstr
from sharedutils import monthlypostcount
from sharedutils import stdlog, dbglog
from plotting import trend_posts_per_day, plot_posts_by_group, pie_posts_by_group

def suffix(d):
    return 'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')

def custom_strftime(fmt, t):
    return t.strftime(fmt).replace('{S}', str(t.day) + suffix(t.day))

friendly_tz = custom_strftime('%B {S}, %Y', dt.now()).lower()

def writeline(file, line):
    '''write line to file'''
    with open(file, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
        f.close()

def howoldami():
    now = dt.now()
    created = dt(2021, 9, 9)
    delta = now - created
    years = delta.days // 365
    months = (delta.days % 365) // 30
    days = (delta.days % 365) % 30
    return f'{years} years, {months} months and {days} days'

def mainpage():
    '''
    main markdown report generator - used with github pages
    '''
    stdlog('generating main page')
    uptime_sheet = 'docs/README.md'
    with open(uptime_sheet, 'w', encoding='utf-8') as f:
        f.close()
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '## summary')
    writeline(uptime_sheet, '_' + friendly_tz + '_')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, 'ransomwatch is currently crawling `' + str(hostcount()) + '` sites belonging to `' + str(groupcount()) + '` unique groups')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '⏲ there have been `' + str(postssince(1)) + '` posts within the `last 24 hours`')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '🦈 there have been `' + str(monthlypostcount()) + '` posts within the `month of ' + currentmonthstr() + '`')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '🪐 there have been `' + str(postssince(90)) + '` posts within the `last 90 days`')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '🏚 there have been `' + str(poststhisyear()) + '` posts within the `year of ' + str(dt.now().year) + '`')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '_⚙️ there are currently `' + str(hostcount(online_only=True)) + '` online hosts & `' + str(parsercount()) + '` custom parsers._')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '🦕 ransomwatch has been running for `' + howoldami() + '` and indexed `' + str(postcount()) + '` posts')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, '_all data ' + ' [(groups)](http://ransomwhat.telemetry.ltd/groups) and [(posts)](http://ransomwhat.telemetry.ltd/posts) is available in JSON (updated hourly)_')
    writeline(uptime_sheet, '')
    writeline(uptime_sheet, "> ransomwatch is fully [open source](https://github.com/joshhighet/ransomwatch#ransomwatch--). please consider [sponsoring](https://github.com/sponsors/joshhighet) if you find it useful!")
    
def indexpage():
    index_sheet = 'docs/INDEX.md'
    with open(index_sheet, 'w', encoding='utf-8') as f:
        f.close()
    groups = openjson('groups.json')
    writeline(index_sheet, '# 📚 index')
    writeline(index_sheet, '')
    header = '| group | title | status | last seen | location |'
    writeline(index_sheet, header)
    writeline(index_sheet, '|---|---|---|---|---|')
    for group in groups:
        dbglog('generating group report for ' + group['name'])
        for host in group['locations']:
            dbglog('generating host report for ' + host['fqdn'])
            if host['available'] is True:
                #statusemoji = '⬆️ 🟢'
                statusemoji = '🟢'
                lastseen = ''
            elif host['available'] is False:
                # iso timestamp converted to yyyy/mm/dd
                lastseen = host['lastscrape'].split(' ')[0]
                #statusemoji = '⬇️ 🔴'
                statusemoji = '🔴'
            if host['title'] is not None:
                title = host['title'].replace('|', '-')
            else:
                title = ''
            line = '| [' + group['name'] + '](https://ransomwatch.telemetry.ltd/#/profiles?id=' + group['name'] + ') | ' + title + ' | ' + statusemoji + ' | ' + lastseen + ' | ' + host['fqdn'] + ' |'
            writeline(index_sheet, line)

def statspage():
    '''
    create a stats page in markdown containing the matplotlib graphs
    '''
    stdlog('generating stats page')
    statspage = 'docs/stats.md'
    # delete contents of file
    with open(statspage, 'w', encoding='utf-8') as f:
        f.close()
    writeline(statspage, '# 📊 stats')
    writeline(statspage, '')
    writeline(statspage, '_timestamp association commenced october 21"_')
    writeline(statspage, '')
    writeline(statspage, '| ![](graphs/postsbygroup7days.png) | ![](graphs/postsbyday.png) |')
    writeline(statspage, '|---|---|')
    writeline(statspage, '![](graphs/postsbygroup.png) | ![](graphs/grouppie.png) |')
    writeline(statspage, '')
    stdlog('stats page generated')

def recentposts(top):
    '''
    create a list the last X posts (most recent)
    '''
    stdlog('finding recent posts')
    posts = openjson('posts.json')
    # sort the posts by timestamp - descending
    sorted_posts = sorted(posts, key=lambda x: x['discovered'], reverse=True)
    # create a list of the last X posts
    recentposts = []
    for post in sorted_posts:
        recentposts.append(post)
        if len(recentposts) == top:
            break
    stdlog('recent posts generated')
    return recentposts

def recentpage():
    '''create a markdown table for the last 100 posts based on the discovered value'''
    fetching_count = 200
    stdlog('generating recent posts page')
    recentpage = 'docs/recentposts.md'
    # delete contents of file
    with open(recentpage, 'w', encoding='utf-8') as f:
        f.close()
    writeline(recentpage, '# 📰 recent posts')
    writeline(recentpage, '')
    writeline(recentpage, '_last `' + str(fetching_count) + '` posts_')
    writeline(recentpage, '')
    writeline(recentpage, '| date | title | group |')
    writeline(recentpage, '|---|---|---|')
    for post in recentposts(fetching_count):
        # show friendly date for discovered
        date = post['discovered'].split(' ')[0]
        # replace markdown tampering characters
        title = post['post_title'].replace('|', '-')
        group = post['group_name'].replace('|', '-')
        urlencodedtitle = urllib.parse.quote_plus(title)
        grouplink = '[' + group + '](https://ransomwatch.telemetry.ltd/#/profiles?id=' + group + ')'
        line = '| ' + date + ' | [`' + title + '`](https://google.com/search?q=' + urlencodedtitle + ') | ' + grouplink + ' |'
        writeline(recentpage, line)
    stdlog('recent posts page generated')

def ddmmyyyy(stamp):
    '''yyyy-mm-dd hh:mm:ss timestamp -> dd/mm/yyyy'''
    year, month, day = stamp.split(' ')[0].split('-')
    return day + '/' + month + '/' + year

def profilepage():
    '''
    create a profile page for each group in their unique markdown files within docs/profiles
    '''
    stdlog('generating profile pages')
    profilepage = 'docs/profiles.md'
    # delete contents of file
    with open(profilepage, 'w', encoding='utf-8') as f:
        f.close()
    writeline(profilepage, '# 🐦 profiles')
    writeline(profilepage, '')
    groups = openjson('groups.json')
    posts_sorted = sorted(openjson('posts.json'), key=lambda x: x['discovered'], reverse=True)
    for group in groups:
        writeline(profilepage, '## ' + group['name'])
        writeline(profilepage, '')
        if group['captcha'] is True:
            writeline(profilepage, ':warning: _has a captcha_')
            writeline(profilepage, '')
        if group['parser'] is True:
            writeline(profilepage, '_parsing : `enabled`_')
            writeline(profilepage, '')
        else:
            writeline(profilepage, '_parsing : `disabled`_')
            writeline(profilepage, '')
        # add notes if present
        if group['meta'] is not None:
            writeline(profilepage, '_`' + group['meta'] + '`_')
            writeline(profilepage, '')
        if group['javascript_render'] is True:
            writeline(profilepage, '> fetching this site requires a headless browser')
            writeline(profilepage, '')
        if group['profile'] is not None:
            for profile in group['profile']:
                writeline(profilepage, '- ' + profile)
                writeline(profilepage, '')
        writeline(profilepage, '| title | available | version | last visit | fqdn')
        writeline(profilepage, '|---|---|---|---|---|')        
        for host in group['locations']:
            # hh:mm dd/mm/yyyy
            time = ':'.join(host['lastscrape'].split(' ')[1].split(':')[:2])
            title = host['title'].replace('|', '-') if host['title'] is not None else 'none'
            line = '| ' + title + ' | ' + str(host['available']) +  ' | ' + str(host['version']) + ' | ' + time + ' ' + ddmmyyyy(host['lastscrape']) + ' | `' + host['fqdn'] + '` |'
            writeline(profilepage, line)
        writeline(profilepage, '')
        writeline(profilepage, '| post | date |')
        writeline(profilepage, '|---|---|')
        for post in posts_sorted:
            if post['group_name'] == group['name']:
                line = '| ' + '`' + post['post_title'].replace('|', '') + '`' + ' | ' + ddmmyyyy(post['discovered']) + ' |'
                writeline(profilepage, line)
        writeline(profilepage, '')
        dbglog('profile page for ' + group['name'] + ' generated')
    stdlog('profile page generation complete')

def main():
    stdlog('generating doco')
    mainpage()
    indexpage()
    recentpage()
    statspage()
    profilepage()
    # if posts.json has been modified within the last 10 mins, assume new posts discovered and recreate graphs
    if os.path.getmtime('posts.json') > (time.time() - 600):
        stdlog('posts.json has been modified within the last 45 mins, assuming new posts discovered and recreating graphs')
        trend_posts_per_day()
        plot_posts_by_group()
        pie_posts_by_group()
        plot_posts_by_group(days=7)
    else:
        stdlog('posts.json has not been modified within the last 45 mins, assuming no new posts discovered')
