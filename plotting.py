#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import datetime
from collections import Counter
import matplotlib.pyplot as plt

from sharedutils import gcount
from sharedutils import openjson

def save(path):
    plt.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.1, transparent=True)
    plt.clf()
    plt.cla()

def sortedgroupcounts(posts):
    '''(groups, counts) - most posts first'''
    group_counts = sorted(gcount(posts).items(), key=lambda x: x[1], reverse=True)
    return [x[0] for x in group_counts], [x[1] for x in group_counts]

def plot_posts_by_group(days=None):
    '''
    plot the number of posts by group in a barchart - all time, or over the last x days
    '''
    posts = openjson('posts.json')
    if days:
        since = datetime.datetime.now() - datetime.timedelta(days=days)
        posts = [post for post in posts if post['discovered'] >= since.strftime('%Y-%m-%d')]
    groups, counts = sortedgroupcounts(posts)
    plt.bar(groups, counts, color="#000000")
    plt.title('posts by group' + (' last ' + str(days) + ' days' if days else ''))
    plt.xlabel('group name')
    plt.xticks(rotation=90)
    plt.ylabel('# of posts')
    save('docs/graphs/postsbygroup' + (str(days) + 'days' if days else '') + '.png')

def trend_posts_per_day():
    '''
    plot the trend of the number of posts per day
    '''
    posts = openjson('posts.json')
    # count of posts per day, i.e {'2021-12-07': 4}
    datecount = Counter(post['discovered'][0:10] for post in posts)
    # remove '2021-09-09' - generic date of import along w/ anything before 2021-08
    datecount.pop('2021-09-09', None)
    datecount = {k: v for k, v in datecount.items() if k >= '2021-08-01'}
    datecount = list(datecount.items())
    datecount.sort(key=lambda x: x[0])
    dates = [datetime.datetime.strptime(x[0], '%Y-%m-%d').date() for x in datecount]
    counts = [x[1] for x in datecount]
    plt.plot(dates, counts, color="#000000")
    plt.title('posts per day')
    plt.xlabel('date')
    plt.xticks(rotation=90)
    plt.ylabel('# of posts')
    save('docs/graphs/postsbyday.png')

def pie_posts_by_group():
    '''
    plot the number of posts by group in a pie
    '''
    groups, counts = sortedgroupcounts(openjson('posts.json'))
    # ignoring the top 10 groups, merge the rest into "other"
    topgroups = groups[:10]
    topcounts = counts[:10]
    othercounts = counts[10:]
    othercount = sum(othercounts)
    topgroups.append('other')
    topcounts.append(othercount)
    colours = ['#ffc09f','#ffee93','#fcf5c7','#a0ced9','#adf7b6','#e8dff5','#fce1e4','#fcf4dd','#ddedea','#daeaf6','#79addc','#ffc09f','#ffee93','#fcf5c7','#adf7b6']
    plt.pie(topcounts, labels=topgroups, autopct='%1.1f%%', startangle=140, labeldistance=1.1, pctdistance=0.8, colors=colours)
    plt.legend(loc='lower center', bbox_to_anchor=(0.5, -0.2), ncol=3)
    plt.text(0.5, 0.5, 'total : ' + str(sum(counts)), horizontalalignment='center', verticalalignment='center', transform=plt.gcf().transFigure)
    plt.title('posts by group')
    save('docs/graphs/grouppie.png')
