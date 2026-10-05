#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
parses the source html for each group where a parser exists & contributed to the post dictionary
always remember..... https://stackoverflow.com/questions/1732348/regex-match-open-tags-except-xhtml-self-contained-tags/1732454#1732454
'''
import os
from sys import platform
from datetime import datetime

from sharedutils import openjson, writejson
from sharedutils import runshellcmd
from sharedutils import todiscord, totweet
from sharedutils import stdlog, dbglog, errlog

# on macOS we use 'grep -oE' over 'grep -oP'
if platform == 'darwin':
    fancygrep = 'grep -oE'
else:
    fancygrep = 'grep -oP'

def posttemplate(victim, group_name, timestamp):
    '''
    assuming we have a new post - form the template we will use for the new entry in posts.json
    '''
    schema = {
        'post_title': victim,
        'group_name': group_name,
        'discovered': timestamp
    }
    dbglog(schema)
    return schema

def existingpost(post_title, group_name):
    '''
    check if a post already exists in posts.json
    '''
    posts = openjson('posts.json')
    # posts = openjson('posts.json')
    for post in posts:
        if post['post_title'] == post_title and post['group_name'] == group_name:
            #dbglog('post already exists: ' + post_title)
            return True
    dbglog('post does not exist: ' + post_title)
    return False

def appender(post_title, group_name):
    '''
    append a new post to posts.json
    '''
    if len(post_title) == 0:
        errlog('post_title is empty')
        return
    # limit length of post_title to 90 chars
    if len(post_title) > 90:
        post_title = post_title[:90]
    if existingpost(post_title, group_name) is False:
        posts = openjson('posts.json')
        newpost = posttemplate(post_title, group_name, str(datetime.today()))
        stdlog('adding new post - ' + 'group:' + group_name + ' title:' + post_title)
        posts.append(newpost)
        dbglog('writing changes to posts.json')
        # writejson keeps utf-8 as is in the case the post contains cyrillic 🇷🇺
        writejson('posts.json', posts)
        # if socials are set try post
        if os.environ.get('DISCORD_WEBHOOK') is not None:
            todiscord(newpost['post_title'], newpost['group_name'], os.environ.get('DISCORD_WEBHOOK'))
        if os.environ.get('DISCORD_WEBHOOK_2') is not None:
            todiscord(newpost['post_title'], newpost['group_name'], os.environ.get('DISCORD_WEBHOOK_2'))
        if os.environ.get('X_CONSUMER_KEY') is not None:
            totweet(newpost['post_title'], newpost['group_name'])

def runparser(parser, group, logname=None):
    '''
    run a shell parser & append each line it returns as a post for the group
    '''
    stdlog('parser: ' + (logname or group))
    posts = runshellcmd(parser)
    if len(posts) == 1:
        errlog(group + ': ' + 'parsing fail')
    for post in posts:
        appender(post, group)

'''
all parsers here are shell - mix of grep/sed/awk & perl - runshellcmd is a wrapper for subprocess.run
'''

def everest():
    parser = '''
    grep '<h2 class="entry-title' source/everest-*.html | cut -d '>' -f3 | cut -d '<' -f1 | sort | uniq
    '''
    runparser(parser, 'everest')


def suncrypt():
    parser = '''
    cat source/suncrypt-*.html | tr '>' '\n' | grep -A1 '<a href="client?id=' | sed -e '/^--/d' -e '/^<a/d' | cut -d '<' -f1 | sed -e 's/[ \t]*$//' "$@" -e '/Read more/d'
    '''
    runparser(parser, 'suncrypt')

def lorenz():
    parser = '''
    grep 'h3' source/lorenz-*.html --no-filename | cut -d ">" -f2 | cut -d "<" -f1 | sed -e 's/^ *//g' -e '/^$/d' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'lorenz')

def lockbit2():
    # egrep -h -A1 'class="post-title"' source/lockbit2-* | grep -v 'class="post-title"' | grep -v '\--' | cut -d'<' -f1 | tr -d ' '
    parser = '''
    awk -v lines=2 '/post-title-block/ {for(i=lines;i;--i)getline; print $0 }' source/lockbit2-*.html | cut -d '<' -f1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort | uniq | grep -v "\.\.\.$"
    '''
    runparser(parser, 'lockbit2')


def arvinclub():
    # grep 'bookmark' source/arvinclub-*.html --no-filename | cut -d ">" -f3 | cut -d "<" -f1
    # grep 'rel="bookmark">' source/arvinclub-*.html -C 1 | grep '</a>' | sed 's/^[^[:alnum:]]*//' | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep '<h1 class="post-title">' source/arvinclub-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    parser = '''
    grep --no-filename  -C 1 '<p><strong>Name:</strong></p>' source/arvinclub-*.html  | grep 'class="highlight"' | cut -d '>' -f 6
    '''
    runparser(parser, 'arvinclub')

def avaddon():
    parser = '''
    grep 'h6' source/avaddon-*.html --no-filename | cut -d ">" -f3 | sed -e s/'<\/a'// -e 's/&amp;/\&/g' | perl -MHTML::Entities -ne 'print decode_entities($_)'
    '''
    runparser(parser, 'avaddon')

def xinglocker():
    parser = '''
    grep "h3" -A1 source/xinglocker-*.html --no-filename | grep -v h3 | awk -v n=4 'NR%n==1' | sed -e 's/^[ \t]*//' -e 's/^ *//g' -e 's/[[:space:]]*$//' -e 's/&amp;/\&/g'
    '''
    runparser(parser, 'xinglocker')

def clop():
    # grep 'PUBLISHED' source/clop-*.html --no-filename | sed -e s/"<strong>"// -e s/"<\/strong>"// -e s/"<\/p>"// -e s/"<p>"// -e s/"<br>"// -e s/"<strong>"// -e s/"<\/strong>"// -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep 'g-menu-item-title' source/clop-*.html --no-filename | sed -e s/'<span class="g-menu-item-title">'// -e s/"<\/span>"// -e 's/^ *//g' -e 's/[[:space:]]*$//' -e 's/^ARCHIVE[[:digit:]]$//' -e s/'^HOW TO DOWNLOAD?$'// -e 's/^ARCHIVE$//' -e 's/^HOME$//' -e '/^$/d'
    parser = '''
    grep '<td><a href="' source/clop-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'clop')

def revil():
    # grep 'href="/posts' source/revil-*.html --no-filename | cut -d '>' -f2 | sed -e s/'<\/a'// -e 's/^[ \t]*//'
    parser = '''
    grep 'justify-content-between' source/revil-*.html --no-filename | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' -e '/ediban/d'
    '''
    runparser(parser, 'revil')

def conti():
    # grep 'class="title">&' source/conti-*.html --no-filename | cut -d ";" -f2 | sed -e s/"&rdquo"//
    parser = '''
    grep 'newsList' source/conti-continewsnv5ot*.html --no-filename | sed -e 's/        newsList(//g' -e 's/);//g' | jq '.[].title' -r  || true
    '''
    runparser(parser, 'conti')
    
def pysa():
    parser = '''
    grep 'icon-chevron-right' source/pysa-*.html --no-filename | cut -d '>' -f3 | sed 's/^ *//g'
    '''
    runparser(parser, 'pysa')

def nefilim():
    parser = '''
    grep 'h2' source/nefilim-*.html --no-filename | cut -d '>' -f3 | sed -e s/'<\/a'// | perl -MHTML::Entities -ne 'print decode_entities($_)'
    '''
    runparser(parser, 'nefilim')

def mountlocker():
    parser = '''
    grep '<h3><a href=' source/mount-locker-*.html --no-filename | cut -d '>' -f5 | sed -e s/'<\/a'// -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'mountlocker')

def babuk():
    parser = '''
    grep '<h5>' source/babuk-*.html --no-filename | sed 's/^ *//g' | cut -d '>' -f2 | cut -d '<' -f1 | grep -wv 'Hospitals\|Non-Profit\|Schools\|Small Business' | sed '/^[[:space:]]*$/d'
    '''
    runparser(parser, 'babuk')
    
def ransomexx():
    # grep 'card-title' source/ransomexx-*.html --no-filename | cut -d '>' -f2 | sed -e s/'<\/h5'// -e 's/^ *//g' -e 's/[[:space:]]*$//' -e 's/&amp;/\&/g'
    parser = '''
    grep '<h2 class="entry-title" itemprop="headline">' source/ransomexx-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'ransomexx')

def cuba():
    # grep '<p>' source/cuba-*.html --no-filename | cut -d '>' -f3 | cut -d '<' -f1
    # grep '<a href="http://' source/cuba-cuba4i* | cut -d '/' -f 4 | sort -u
    parser = '''
    grep --no-filename '<a href="/company/' source/cuba-*.html | cut -d '/' -f 3 | cut -d '"' -f 1 | sort --uniq | grep -v company
    '''
    runparser(parser, 'cuba')

def pay2key():
    parser = '''
    grep 'h3><a href' source/pay2key-*.html --no-filename | cut -d '>' -f3 | sed -e s/'<\/a'//
    '''
    runparser(parser, 'pay2key')

def azroteam():
    parser = '''
    grep "h3" -A1 source/aztroteam-*.html --no-filename | grep -v h3 | awk -v n=4 'NR%n==1' | sed -e 's/^[ \t]*//' -e 's/&amp;/\&/g'
    '''
    runparser(parser, 'azroteam')

def lockdata():
    parser = '''
    grep '<a href="/view.php?' source/lockdata-*.html --no-filename | cut -d '>' -f2 | cut -d '<' -f1
    '''
    runparser(parser, 'lockdata')
    
def blacktor():
    # sed -n '/tr/{n;p;}' source/bl@cktor-*.html | grep 'td' | cut -d '>' -f2 | cut -d '<' -f1
    parser = '''
    grep '>Details</a></td>' source/blacktor-*.html --no-filename | cut -f2 -d '"' | cut -f 2- -d- | cut -f 1 -d .
    '''
    runparser(parser, 'blacktor')
    
def darkleakmarket():
    parser = '''
    grep 'page.php' source/darkleakmarket-*.html --no-filename | sed -e 's/^[ \t]*//' | cut -d '>' -f3 | sed '/^</d' | cut -d '<' -f1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'darkleakmarket')

def blackmatter():
    parser = '''
    grep '<h4 class="post-announce-name" title="' source/blackmatter-*.html --no-filename | cut -d '"' -f4 | sort -u
    '''
    runparser(parser, 'blackmatter')

def payloadbin():
    parser = '''
    grep '<h4 class="h4' source/payloadbin-*.html --no-filename | cut -d '>' -f3 | cut -d '<' -f 1 | perl -MHTML::Entities -ne 'print decode_entities($_)'
    '''
    runparser(parser, 'payloadbin')

def groove():
    parser = '''
    egrep -o 'class="title">([[:alnum:]]| |\.)+</a>' source/groove-*.html | cut -d '>' -f2 | cut -d '<' -f 1
    '''
    runparser(parser, 'groove')

def karma():
    parser = '''
    grep "h2" source/karma-*.html --no-filename | cut -d '>' -f 3 | cut -d '<' -f 1 | sed '/^$/d'
    '''
    runparser(parser, 'karma')

def blackbyte():
    # grep "h1" source/blackbyte-*.html --no-filename | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e '/^$/d' -e 's/[[:space:]]*$//'
    # grep "display-4" source/blackbyte-*.html --no-filename | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^[ \t]*//' -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep '<h1 class="h_font"' source/blackbyte-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    # grep --no-filename 'class="h_font"' source/blackbyte-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e '/^$/d' -e 's/[[:space:]]*$//'
    parser = '''
    grep --no-filename 'class="target-name"' source/blackbyte-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e '/^$/d' -e 's/[[:space:]]*$//' 
    '''
    runparser(parser, 'blackbyte')

def spook():
    parser = '''
    grep 'h2 class' source/spook-*.html --no-filename | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e '/^$/d'
    '''
    runparser(parser, 'spook')

def quantum():
    parser = '''
    awk '/h2/{getline; print}' source/quantum-*.html | sed -e 's/^ *//g' -e '/<\/a>/d' -e 's/&amp;/\&/g' | perl -MHTML::Entities -ne 'print decode_entities($_)'
    '''
    runparser(parser, 'quantum')

def atomsilo():
    parser = '''
    grep "h4" source/atomsilo-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'atomsilo')
        
def lv():
    # %s "blog-post-title.*?</a>" source/lv-rbvuetun*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    parser = '''
    jq -r '.posts[].title' source/lv-rbvuetun*.html | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'lv')

def midas():
    parser = '''
    grep "/h3" source/midas-*.html --no-filename | sed -e 's/<\/h3>//' -e 's/^ *//g' -e '/^$/d' -e 's/^ *//g' -e 's/[[:space:]]*$//' -e '/^$/d' -e 's/&amp;/\&/g'
    '''
    runparser(parser, 'midas')

def snatch():
    parser = '''
    %s "a-b-n-name.*?</div>" source/snatch-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sort | uniq | sed 's/&amp;/\&/g'
    ''' % (fancygrep)
    runparser(parser, 'snatch')

def rook():
    parser = '''
    grep 'class="post-title"' source/rook-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed '/^&#34/d'
    '''
    runparser(parser, 'rook')

def cryp70n1c0d3():
    parser = '''
    grep '<td class="selection"' source/cryp70n1c0d3-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'cryp70n1c0d3')

def mosesstaff():
    parser = '''
    grep '<h2 class="entry-title">' source/moses-moses-staff.html -A 3 --no-filename | grep '</a>' | sed 's/^ *//g' | cut -d '<' -f 1 | sed 's/[[:space:]]*$//'
    '''
    runparser(parser, 'mosesstaff')

def alphv():
    # egrep -o 'class="mat-h2">([[:alnum:]]| |\.)+</h2>' source/alphv-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    # grep -o 'class="mat-h2">[^<>]*<\/h2>' source/alphv-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' -e '/No articles here yet, check back later./d'
    parser = '''
    jq -r '.items[].title' source/alphv-alphvuzxyxv6yl*.html | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' || true
    '''
    runparser(parser, 'alphv')

def nightsky():
    parser = '''
    grep 'class="mdui-card-primary-title"' source/nightsky-*.html --no-filename | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'nightsky')

def vicesociety():
    # grep '<tr><td valign="top"><br><font size="4" color="#FFFFFF"><b>' source/vicesociety-*.html --no-filename | cut -d '>' -f 6 | cut -d '<' -f 1 | sed -e '/ato District Health Boa/d' -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort --uniq
    parser = '''
    grep '<tr><td valign="top"><br><font color="#FFFFFF" size="4">' source/vicesociety-*.html --no-filename | cut -d '>' -f 6 | cut -d '<' -f 1 | sed -e '/ato District Health Boa/d' -e 's/^ *//g' -e 's/[[:space:]]*$//' -e 's/&amp;/\&/g' | sort --uniq
    '''
    runparser(parser, 'vicesociety')

def pandora():
    parser = '''
    grep '<span class="post-title gt-c-content-color-first">' source/pandora-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed 's/&amp;/\&/g'
    '''
    runparser(parser, 'pandora')

def stormous():
    # grep '<p> <h3> <font color="' source/stormous-*.html | grep '</h3>' | cut -d '>' -f 4 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep '<h3>' source/stormous-*.html | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | grep "^<h3> <font" | cut -d '>' -f 3 | cut -d '<' -f 1 | sed 's/[[:space:]]*$//'
    # awk '/<h3>/{getline; print}' source/stormous-*.html | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep 'class="h1"' source/stormous-h3*.html | cut -d '>' -f 4 | cut -d '<' -f 1 | sort --uniq | sed -e '/^Percentage/d' -e '/^Payment/d' -e '/^Click here/d'
    # grep --no-filename ' <a href="">  <h3>' source/stormous-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    ###
    # descripotion [current] : grep --no-filename '<p class="description" style="color: rgb(59, 52, 52);"> ' source/stormous-*.html | cut -d '>' -f 2 | sed -e 's/\.[^.]*$//' -e 's/^ *//'
    # grep --no-filename '<td><center><a href="#"  width="120px"><img src="' source/stormous-*.html | cut -d '"' -f 6 | cut -d '/' -f 3 | sed 's/\.[^.]*$//' | grep -v '^$' && grep '<td><a href="' source/stormous-ransekgbpi*.html | cut -d '"' -f 2 | sort | uniq
    parser = '''
    grep --no-filename -Eo '<td>www\.[^<]*</td>' source/stormous-*.html | sed -E 's/<td>(www\.[^<]*)<\/td>/\\1/'
    '''
    runparser(parser, 'stormous')

def leaktheanalyst():
    parser = '''
    grep '<label class="news-headers">' source/leaktheanalyst-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/Section //' -e 's/#//' -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort -n | uniq
    '''
    runparser(parser, 'leaktheanalyst')

def blackbasta():
    # egrep -o 'fqd.onion/\?id=([[:alnum:]]| |\.)+"' source/blackbasta-*.html | cut -d = -f 2 | cut -d '"' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep '.onion/?id=' source/blackbasta-st*.html | cut -d '>' -f 52 | cut -d '<' -f 1 | sed -e 's/\&amp/\&/g' -e 's/\&;/\&/g'
    # grep '.onion/?id=' source/blackbasta-st*.html | cut -d '>' -f 52 | cut -d '=' -f 5 | cut -d '"' -f 1 | sed -e 's/^ *//g' -e '/^$/d' -e 's/[[:space:]]*$//'
    # cat source/blackbasta-*.html | grep -Eo '\?id=[^"]+' | awk -F'=' '{print $2}' | sed -e 's/\&amp;/\&/g'
    # grep '<strong>SITE:</strong> <em>' source/blackbasta-*.html | cut -d '>' -f 5 | cut -d '<' -f 1
    parser = '''
    grep '<p data-v-md-line="3"><em>' source/blackbasta-stnii*.html  | cut -d '>' -f 4 | cut -d '<' -f 1
    '''
    runparser(parser, 'blackbasta')

def onyx():
    # grep '<h6 class=' source/onyx-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e '/Connect with us/d' -e 's/^ *//g' -e 's/[[:space:]]*$//'
    parser = '''
    grep '<h6>' source/onyx-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e '/^[[:space:]]*$/d' -e '/Connect with us/d'
    '''
    runparser(parser, 'onyx')

def mindware():
    parser = '''
    grep '<div class="card-header">' source/mindware-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'mindware')

def ransomhouse():
    parser = '''
    egrep -o "class=\"cls_recordTop\"><p>([A-Za-z0-9 ,\'.-])+</p>" source/ransomhouse-xw7au5p*.html | cut -d '>' -f 3 | cut -d '<' -f 1 && jq -r '.data[].header' source/ransomhouse-zoh*.html || true
    '''
    runparser(parser, 'ransomhouse')

def cheers():
    parser = '''
    grep '<a href="' source/cheers-*.html | grep -v title | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e '/Cheers/d' -e '/Home/d' -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'cheers')

def lockbit3():
    # grep '<div class="post-title">' source/lockbit3-*.html -C 1 --no-filename | grep '</div>' | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort --uniq | tr '[:upper:]' '[:lower:]'
    # grep --no-filename '<div class="post-title">' source/lockbit3-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    parser = '''
    grep '<div class="post-title">' source/lockbit3-*.html -C 1 --no-filename | grep '</div>' | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort --uniq | tr '[:upper:]' '[:lower:]'
    '''
    runparser(parser, 'lockbit3')
        
def lockbit3fs():
    # a rather crude parser that tries to exclude based on existing indexed posts on leaksites to catch others
    parser = '''
    grep --no-filename '<tr><td class="link">' source/lockbit3_fs-*.html | cut -d '"' -f 6 | sort | uniq | grep -viFx "$(jq -r '.[] | select(.group_name == "lockbit2" or .group_name == "lockbit3") | .post_title | ascii_downcase' posts.json)"
    '''
    runparser(parser, 'lockbit3_fs', logname='lockbit3fs')

def yanluowang():
    parser = '''
    grep '<a href="/posts' source/yanluowang-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | perl -MHTML::Entities -ne 'print decode_entities($_)'
    '''
    runparser(parser, 'yanluowang')

def omega():
    parser = '''
    grep "<tr class='trow'>" -C 1 source/0mega-*.html | grep '<td>' | cut -d '>' -f 2 | cut -d '<' -f 1 | sort --uniq
    '''
    runparser(parser, '0mega')

def bianlian():
    # sed -n '/<a href="\/companies\//,/<\/a>/p' source/bianlian-*.html | egrep -o "([A-Za-z0-9 ,\'.-])+</a>" | cut -d '<' -f 1 | sed -e '/Contacts/d'
    parser = '''
    sed -n '/<a href="\/companies\//,/<\/a>/p' source/bianlian-*.html | sed 's/&amp;/and/' | egrep -o "([A-Za-z0-9 ,*\'.-])+</a>" | cut -d '<' -f 1 | sed -e '/Contacts/d' -e '/BianLian/d' -e '/Home/d' | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'bianlian')

def redalert():
    parser = '''
    egrep -o "<h3>([A-Za-z0-9 ,\'.-])+</h3>" source/redalert-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'redalert')

def daixin():
    parser = '''
    grep '<h4 class="border-danger' source/daixin-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e '/^$/d' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'daixin')

def icefire():
    parser = '''
    grep align-middle -C 2 source/icefire-*.html | grep span | grep -v '\*\*\*\*' | grep -v updating | grep '\*\.' | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'icefire')

def donutleaks():
    parser = '''
    grep '<h2 class="post-title">' source/donutleaks-*.html --no-filename | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/\&amp;/\&/g'
    '''
    runparser(parser, 'donutleaks')
        
def sparta():
    parser = '''
    grep 'class="card-header d-flex justify-content-between"><span>' source/sparta-*.html | cut -d '>' -f 4 | cut -d '<' -f 1 | sed -e '/^[[:space:]]*$/d' && grep '<div class="card-header d-flex justify-content-between"><span>' source/sparta-*.html | grep -v '<h2>' | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'sparta')

def qilin():
    # kbsq[...]faad.onion/api/public/blog/list
    # # jq '.[].target_utl' -r source/qilin-kb*.html || true
    # grep 'class="item_box-info__link"' source/qilin-kb*.html | cut -d '"' -f 2 | sed '/#/d'
    parser = '''
    grep '<a href="/site/view?uuid=' source/qilin-kbsq*.html | grep item_box | cut -d '<' -f 2 | cut -d '>' -f 2
    '''
    runparser(parser, 'qilin')

def shaoleaks():
    parser = '''
    grep '<h2 class="entry-title' source/shaoleaks-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'shaoleaks')

def mallox():
    # grep 'class="card-title"' source/mallox-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    parser = '''
    sed -n '/fs-3 fw-bold text-gray-900 mb-2/{n;s/^[[:space:]]*//;s/[[:space:]]*<\/div>.*$//p;}' source/mallox-*.html | sort -u
    '''
    runparser(parser, 'mallox')
    
def royal():
    parser = '''
    jq -r '.data[].url' source/royal-royal4ezp7xr*.html || true
    '''
    runparser(parser, 'royal')

def projectrelic():
    parser = '''
    grep --no-filename '<div class="website">' source/projectrelic-*.html | cut -d '"' -f 4
    '''
    runparser(parser, 'projectrelic')

def ransomblog_noname():
    parser = '''
    grep --no-filename '<h2 class="entry-title default-max-width">' source/ransomblog_noname-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'ransomblog_noname')
        
def medusa():
    # cat source/medusa-medusaxko7*.html | jq -r '.list[].company_name' || true
    parser = '''
    cat source/medusa-xf*.html | jq -r '.list[].company_name' | perl -MHTML::Entities -ne 'print decode_entities($_)' || true
    '''
    runparser(parser, 'medusa')

def nokoyawa():
    # awk '/<h1/{getline; print}' source/nokoyawa-*.html | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    parser = '''
    jq -r '.payload[].title' source/nokoyawa-noko65rm*.html | sed 's/%20/ /g'
    '''
    runparser(parser, 'nokoyawa')

def dataleak():
    parser = '''
    grep '<h2 class="post-title">' source/dataleak-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'dataleak')

def monti():
    parser = '''
    grep '<h5 style="color:#dbdbdb" >' source/monti-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | grep -v test | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'monti')

def play():
    # %s --no-filename '(?<=\\"\\").*?(?=div)' source/play-*.html | tr -d '<>' | tr -d \\'
    parser = '''
    cat source/play-*.html | tr '>' '\n' | grep -A 1 'onclick="viewtopic' | grep -v 'click to open' | grep -v '\-\-' | cut -d '<' -f 1 | sort -u
    '''
    runparser(parser, 'play')

def karakurt():
    parser = '''
    grep '<a href="/companies/' source/karakurt-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e '/^[[:space:]]*$/d' -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'karakurt')

def unsafeleak():
    parser = '''
    egrep -o "<h4>([A-Za-z0-9 ,\'.-])+</h4>" source/unsafeleak-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'unsafeleak')

def freecivilian():
    # grep "class=\\"a_href\\">" source/freecivilian-*.html |  sed 's/<[^>]*>//g; s/^[ \t]*//; s/[ \t]*$//; s/+ //;'
    parser = '''
    grep '<a class="a_href">' source/freecivilian-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'freecivilian')

def vendetta():
    parser = '''
    grep --no-filename '<a href="/company/' source/vendetta-*.html | cut -d '/' -f 3 | cut -d '"' -f 1 | sort --uniq | grep -v company
    '''
    runparser(parser, 'vendetta')

def abyss():
    parser = '''
    grep "'title'" source/abyss-*.html | cut -d "'" -f 4
    '''
    runparser(parser, 'abyss')

def moneymessage():
    parser = '''
    cat source/moneymessage-*.html | jq -r 'map(select(.data.name != null) | .data.name) | join(" ")' || true
    '''
    runparser(parser, 'moneymessage')

def dunghill_leak():
    # awk '/<div class="ibody_title">/{print $0; getline; print $0}' source/dunghill_leak-*.html | sed -e 'N;s/\n//g' -e 's/<div class="ibody_title">//g' -e 's/<\/div>//g' -e 's/[[:space:]]*<\/a>.*$//g' -e 's/[[:space:]]\+/ /g' -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep -C 1 '<div class="ibody_title">' source/dunghill_leak-*.html | grep -v '</div>' | grep -v '<div class="ibody_title">' | grep -v '\-\-' | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | grep -v '</a>'
    # grep -C 3 '<div class="ibody_body">' source/dunghill_leak-*.html | grep strong | sed -E 's/.*<strong>([^<]+)<\/strong>.*/\\1/'
    parser = '''
    grep -C 3 '<div class="ibody_body">' source/dunghill_leak-p66slx*.html | grep '<strong>' | sed -e 's/<p>/ /' | cut -d '>' -f 2 | cut -d '<' -f 1 | sort | uniq || true
    '''
    runparser(parser, 'dunghill_leak')

def trigona():
    # awk -vRS='</a><a class="auction-item-info__external"' '{gsub(/.*<div class="auction-item-info__title"> <a href="[^"]*" title="">|<\/a>.*/,""); print}' source/trigona-*.html | grep -v href | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
    # grep -o -E '<a href="/leak/[0-9]+" title="">[^<]*' source/trigona-*.html | sed -E 's/<a href="\/leak\/[0-9]+" title="">//'
    # grep -o '<a [^>]*title="[^"]*"' source/trigona-*.html | grep 'path=' | cut -d '=' -f 3 | cut -d '"' -f 1
    # jq -r '.data.leaks[].external_link' source/trigona-trigonax2*.html || true
    parser = '''
    jq -r '.data.leaks[].external_link' source/trigona-krs*.html || true
    '''
    runparser(parser, 'trigona')

def crosslock():
    parser = '''
    grep '<div class="post-date">' source/crosslock-*.html --no-filename | grep -o 'a href.*' | cut -d'>' -f2 | sed 's/<\/a//'
    '''
    runparser(parser, 'crosslock')

def akira():
    # gsub used as title fields contain newlines
    parser = '''
    jq -j '.[] | .title |= gsub("\n"; " ") | .title, "\n"' source/akira-*.html | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'akira')

def cryptnet():
    parser = '''
    grep '<h3 class="blog-subject">' source/cryptnet-blog*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, 'cryptnet')

def ragroup():
    # grep --no-filename '<a href="/posts/' source/ragroup-*.html | cut -d '/' -f 3 | cut -d '"' -f 1
    # grep --no-filename '<div class="portfolio-content">' source/ragroup-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    parser = '''
    grep --no-filename '<div class="portfolio-content">' source/ragroup-*.html | grep -v PUBLISHED | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'ragroup')

def eightbase():
    # awk '/class="stretched-link">/{getline; print}' source/8base-*.html | sed -e 's/^[ \t]*//' | sort | uniq
    # awk '/class="stretched-link">/{getline; print}' source/8base-*.html | sed -e 's/^[ \t]*//' | perl -MHTML::Entities -ne 'print decode_entities(decode_entities($_))' | sort | uniq
    parser = '''
    awk '/class="stretched-link">/{getline; print}' source/8base-*.html | sed -e 's/^[ \t]*//' -e 's/^ *//g' -e 's/[[:space:]]*$//' | perl -MHTML::Entities -ne 'print decode_entities(decode_entities($_))' | sort | uniq  | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    '''
    runparser(parser, '8base')

def malas():
    parser = '''
    grep '<a class="link" href=' source/malas-*.html  --no-filename | cut -d '>' -f2
    '''
    runparser(parser, 'malas')

def blacksuit():
    parser = "sed 's/>/>\\n/g' source/blacksuit-*.html | grep -A 1 '<div class=\"url\">' | grep href | cut -d '\"' -f 2 | sort | uniq"
    runparser(parser, 'blacksuit')

def rancoz():
    parser = '''
    grep -C 1 "<tr class='trow'>" source/rancoz-*.html | grep '<td>' | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'rancoz')

def darkrace():
    parser = '''
    egrep -o '<a class="post-title-link" href="/[^"]+">[^<]+' source/darkrace-*.html | cut -d'>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'darkrace')

def rhysida():
    parser = '''
    grep "m-2 h4" source/rhysida-* | cut -d '>' -f 3 | cut -d '<' -f 1 
    '''
    runparser(parser, 'rhysida')
        
def noescape():
    # grep -oe "target=\\"_blank\\">[^<]*" source/noescape*wzttad.html | cut -d'>' -f2
    parser = '''
    grep -o '<a[^>]*title="[^"]*"[^>]*>' source/noescape-*wzttad.html | sed -e 's/<a[^>]*title="//' -e 's/".*//' | awk -F'"' '{print $1}' | awk -F'"' '!/Twitter/{print $1}'
    '''
    runparser(parser, 'noescape')

def cactus():
    parser = '''
    %s '<a .*? href=".*?/posts/.*?".*?</h2></a>' source/cactus-*.html | %s '<h2.*?>(.*?)</h2>' | cut -d'>' -f2 | cut -d'<' -f1
    ''' % (fancygrep, fancygrep)
    runparser(parser, 'cactus')

def knight():
    # jq -r '.pages[].name' source/knight-knight3xppu*.html || true
    parser = '''
    jq -r '.posts[].title' source/knight-knight3*.html || true
    '''
    runparser(parser, 'knight')

def incransom():
    # jq -r '.payload[].title' source/incransom-incback*.html | sed -e 's/%20/ /' || true
    # jq -r '.payload[].title' source/incransom-incback*.html | perl -MURI::Escape -ne 'print uri_unescape($_)' | sort | uniq || true
    parser = '''
    jq -r 'map(select(type == "object")) | .[].announcements[].company.company_name' source/incransom-incbacg6*.html | perl -MURI::Escape -ne 'print uri_unescape($_)' | sort | uniq || true
    '''
    runparser(parser, 'incransom')

def metaencryptor():
    parser = '''
    grep '<a class="btn btn-secondary btn-sm" href="' source/metaencryptor-*.html | cut -d '>' -f 18 | grep btn-sm | cut -d '"' -f 4
    '''
    runparser(parser, 'metaencryptor')

def cloak():
    parser = '''
    grep '<h2 class="main__name">' source/cloak-cloak7jp*.html --no-filename | cut -d ">" -f2 | cut -d '<' -f 1 && grep --no-filename -A 1 '<div class="card-body">' source/cloak-cloak.html | grep '<p class="card-text">' | cut -d '>' -f 2 | cut -d '<' -f 1 | grep -v test || true
    '''
    runparser(parser, 'cloak')

def ransomedvc():
    # grep -A 1 '<div class="card">' source/ransomedvc-f6amq3izz*.html | grep '<b><u>' | cut -d '>' -f 3 | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//'
    # grep 'class="alignwide wp-block-post-title' source/ransomedvc-f6amq3*ad.html | cut -d '>' -f 4 | cut -d '<' -f 1
    # grep --no-filename -A 1 '<div class="card">' source/ransomedvc-*.html | grep '#ff5353' | cut -d '>' -f 3 | cut -d '<' -f 1 | sort | uniq
    parser = '''
    grep --no-filename '<b><u>' source/ransomedvc-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 | sort -u
    '''
    runparser(parser, 'ransomedvc')

def ciphbit():
    parser = '''
    grep '<h2><a class="title"' source/ciphbit-*.html | cut -d '"' -f 4
    '''
    runparser(parser, 'ciphbit')

def threeam():
    # grep -A 1 '<div class="p ost-title-block">' source/threeam-*.html | grep '<div>' | cut -d '>' -f 2 | cut -d '<' -f 1
    # cat source/threeam-*.html | awk '{while (match($0, /<div id="post-title" class="post-title f_left">[^<]+<\/div>/)) {print substr($0, RSTART, RLENGTH); $0 = substr($0, RSTART + RLENGTH); fflush()}}' | cut -d '>' -f 2 | cut -d '<' -f 1
    parser = '''
    perl -lne 'while(/<div id="post-title" class="post-title f_left">([^<]+)<\/div>/g) { print $1 }' source/threeam-*.html || true
    '''
    runparser(parser, 'threeam')

def cryptbb():
    parser = '''
    grep -A 1 'class="stretched-link">' source/cryptbb-*.html | grep -v '<a href="' | grep -v '\-\-' | sed -e '/^[[:space:]]*$/d' -e 's/^ *//g'
    '''
    runparser(parser, 'cryptbb')

def losttrust():
    parser = '''
    grep -o '<div class="card-header">[^<]*</div>' source/losttrust-*.html  | sed -e 's/<[^>]*>//g' -e 's/&amp;/\&/g'
    '''
    runparser(parser, 'losttrust')

def meow():
    # jq -r '.data[].title' source/meow-totos*.html || true
    parser = '''
    jq -r '.data.posts[].title' source/meow-meow6x*.html || true
    '''
    runparser(parser, 'meow')

def dragonforce():
    # grep -o 'href="https://[^"]*' source/dragonforce-*.html | sed 's/href="//'
    parser = '''
    cat source/dragonforce-z3wqggtxft*.html | jq ".data.publications.[].site" -r || true
    '''
    runparser(parser, 'dragonforce')
  
def werewolves():
    parser = '''
    grep --no-filename '<!-- </a> -->' source/werewolves-*.html | cut -d '<' -f 1 | sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort | uniq
    '''
    runparser(parser, 'werewolves')

def malekteam():
    parser = '''
    grep --no-filename '<div class="timeline_date-text"><span class="text-danger">' source/malekteam-*.html | cut -d '>' -f 4 | cut -d '<' -f 1 |  sed -e 's/^ *//g' -e 's/[[:space:]]*$//' | sort | uniq
    '''
    runparser(parser, 'malekteam')
        
def insane():
    parser = '''
    grep --no-filename 'class="button button2"' source/insane-*.html | cut -d '>' -f 5 | cut -d '<' -f 1 | sort | uniq | grep -Ev 'A black man|Going Insane Ransomware Main page|Cat'
    '''
    runparser(parser, 'insane')

def slug():
    parser = '''
    grep ' <title type="html">' source/slug-*.html | cut -d '[' -f 3 | cut -d ']' -f 1
    '''
    runparser(parser, 'slug')
        
def ransomblog_noname2():
    parser = '''
    cat source/ransomblog_noname2-*.html | tr '>' '\n' | grep -A 2 'target="_self" rel="bookmark noopener noreferrer"' | grep -Ev '.onion/wp/|\[NEGOTIATED\]</a|</h4|\-\-' | cut -d '<' -f 1
    '''
    runparser(parser, 'ransomblog_noname2')

def alphalocker():
    # grep '<a href="blog_1-11"' source/alphalocker-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | grep -v Read || true
    parser = '''
    grep '<div class="news_title" style="display:inline-block; width:100%;">' -C 2 source/alphalocker-mydatae2d*.html | grep '<a href="blog_1' | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'alphalocker')

def ransomhub():
    # grep '<h5 class="card-title">' source/ransomhub-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 | perl -MHTML::Entities -ne 'print decode_entities($_)'
    parser = '''
    grep '<div class="card-title text-center">' source/ransomhub-ransomxifxw*.html | cut -d '>' -f 3 | cut -d '<' -f 1 && grep '<tr><td class="link">' source/ransomhub-fp*.html | cut -d '"' -f 4 | sort --uniq
    '''
    runparser(parser, 'ransomhub')

def blackout():
    parser = '''
    grep -oE '<a[^>]*class="[^"]*link-offset-2 link-underline link-underline-opacity-0 text-white[^"]*"[^>]*>[^<]+</a>' source/blackout-*.html | sed -E 's/.*>([^<]+)<\/a>/\\1/'
    '''
    runparser(parser, 'blackout')

def donex():
    parser = '''
    grep '<a class="post-title"' source/donex-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | tr '[:upper:]' '[:lower:]' || true
    '''
    runparser(parser, 'donex')

def killsecurity():
    # grep '<div class="post-title">' source/killsecurity-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    parser = '''
    grep '<st><img src="static/svg/' source/killsecurity-ks54*.html | cut -d ';' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'killsecurity')

def redransomware():
    parser = '''
    grep '<h4 class="card-header">' source/redransomware-*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'redransomware')

def darkvault():
    parser = '''
    cat source/darkvault-*.html | awk 'BEGIN{RS="<div class=\\"post-title\\">"; FS="</div>"} NR>1 {print $1}' || true
    '''
    runparser(parser, 'darkvault')

def hellogookie():
    parser = '''
    awk '/<h5 class="card-title">/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print}' source/hellogookie-*.html || true
    '''
    runparser(parser, 'hellogookie')

def apt73():
    parser = '''
    grep "class='segment__text__off'" source/apt73-*.html | sed -n "s/.*<div class='segment__text__off'>\([^<]*\)<\/div.*/\\1/p"
    '''
    runparser(parser, 'apt73')

def qiulong():
    parser = '''
    grep '<h1 class="entry-title">' source/qiulong-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'qiulong')

def embargo():
    parser = '''
    awk 'BEGIN{RS="<div class=\\"text-2xl font-bold\\">"; FS="</div>"} NR>1 {print $1}' source/embargo-embargobe*.html || true
    '''
    runparser(parser, 'embargo')

def dAn0n():
    # '<h2 class="card-title">'
    # grep '<h4 class="card-title">' source/dAn0n-2c7nd*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | perl -MURI::Escape -ne 'print uri_unescape($_)' | perl -MURI::Escape -ne 'print uri_unescape($_)'
    parser = '''
    grep '<h6 class="card-title"' source/dAn0n-2c7nd*.html | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'dAn0n')

def underground():
    parser = '''
    grep -A 1 '<span>Name: </span>' source/underground-*.html | grep '<p>' | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'underground')

def spacebears():
    parser = '''
    grep href source/spacebears-*.html | grep '.onion/companies/' | cut -d '>' -f 2 | cut -d '<' -f 1
    '''
    runparser(parser, 'spacebears')

def flocker():
    parser = '''
    grep '<h2 class="entry-title ast-blog-single-element"' source/flocker-flock*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'flocker')

def arcusmedia():
    parser = '''
    grep '<h2 class="entry-title mb-half-gutter last:mb-0">' source/arcusmedia-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'arcusmedia')

def trinity():
    parser = '''
    grep -A 1 '<strong>Company name:</strong>' source/trinity-*.html | grep -v '<strong>' | grep -v '<p>' | grep -v -- '--' | sed -e 's/^ *//g'
    '''
    runparser(parser, 'trinity')

def sensayq():
    parser = '''
    grep '<div class="cls_recordTop">' source/sensayq-*.html | cut -d '>' -f 3 | cut -d '<' -f 1
    '''
    runparser(parser, 'sensayq')

def cicada3301():
    parser = '''
    grep -C 1 'tracking-widest">web:</span>' source/cicada3301-*.html | grep href | cut -d '"' -f 2
    '''
    runparser(parser, 'cicada3301')

def pryx():
    parser = '''
    grep '<td><a href="' source/pryx-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 | sed 's/\[\*\] //g' | grep -v soon || true
    '''
    runparser(parser, 'pryx')

def braincipher():
    parser = '''
    grep 'class="h5">' source/braincipher-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | sort -u | sed '/More important than money, only honor./d' | sed '/Space for your advertising./d' | sed '/Very expensive advertising./d'
    '''
    runparser(parser, 'braincipher')

def FOG():
    parser = '''
    grep '<p class="pb-4 text-lg font-bold">' source/FOG-*.html | cut -d '>' -f 10 | cut -d '<' -f 1 | grep -v 00 || true
    '''
    runparser(parser, 'FOG')
        
def handala():
    parser = '''
    grep '<h2 class="wp-block-post-title">' source/handala-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 | sort | uniq || true
    '''
    runparser(parser, 'handala')

def eldorado():
    # grep '<h1 class="text-xl mb-2 text-decoration-underline">' source/eldorado-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 || true
    # grep '<p class="u-align-center u-text u-text-' source/eldorado-*.html | grep -v 'data-lang-' | cut -d '>' -f 2 | cut -d '<' -f 1 | sed -e 's/<[^>]*>//g' -e 's/^[ \t]*//' || true
    parser = '''
    grep 'const projects =' source/eldorado-*.html | sed 's/const projects =\(.*\);/\\1/' | jq '.[].name' -r || true
    '''
    runparser(parser, 'eldorado')

def vanirgroup():
    parser = '''
    grep '</pre></p></div><p data-v-' source/vanirgroup-*.html | awk 'match($0, /projectName:"[^"]+"/) {while (match($0, /projectName:"[^"]+"/)) {print substr($0, RSTART+13, RLENGTH-14); $0 = substr($0, RSTART+RLENGTH)}}' || true
    '''
    runparser(parser, 'vanirgroup')

def ransomcortex():
    parser = '''
    grep '<h2 class="entry-title">' source/ransomcortex-*.html | cut -d '>' -f 3 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'ransomcortex')

def madliberator():
    parser = '''
    grep '<span class="blog-cat">' source/madliberator-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'madliberator')

def dispossessor():
    parser = '''
    cat source/dispossessor-e27z5*.html | jq '.data.items[].company_name' -r || true
    '''
    runparser(parser, 'dispossessor')

def nullbulge():
    parser = '''
    grep '<div class="elem">' -A1 source/nullbulge-nullbulge.html | grep '<h6 class="hacked__font">' | cut -d '>' -f 2 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'nullbulge')

def lynx():
    parser = '''
    jq -r '.payload.announcements[].company.company_name' source/lynx-lynxblog.html | perl -MURI::Escape -ne 'print uri_unescape($_)' || true
    '''
    runparser(parser, 'lynx')

def helldown():
    parser = '''
    grep --no-filename '<p class="card-summary">' source/helldown*.html | sed -e 's/<[^>]*>//g' -e 's/^[ \t]*//' | grep -v 'password is required to continue reading.' | sort | uniq || true
    '''
    runparser(parser, 'helldown')

def orca():
    parser = '''
    grep '<h2 class="blog__card-top-info-title">' source/orca-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 | grep -v INTRODUCT || true
    '''
    runparser(parser, 'orca')

def nitrogen():
    parser = '''
    grep -C 1 '<div class="w3-half "' source/nitrogen-*.html  | grep h3 | cut -d '>' -f 3 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'nitrogen')

def sarcoma():
    parser = '''
    grep '<div class="card-title text-center fs-5">' source/sarcoma-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'sarcoma')
        
def interlock():
    parser = '''
    grep '<div class="advert_info_title">' source/interlock-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'interlock')

def hellcat():
    parser = '''
    grep '<h2>' source/hellcat-*.html | cut -d '>' -f 2 | cut -d '<' -f 1 || true
    '''
    runparser(parser, 'hellcat')

def termite():
    parser = '''
    jq -r '.[].title' source/termite-termitelfv*.html || true
    '''
    runparser(parser, 'termite')

def kairos():
    parser = '''
    grep -C 2 '<div class="desc">' source/kairos-*.html | grep -v '<div class="desc">' | sed -e '/^$/d' -e 's/--//g' -e 's/^[[:space:]]*//;s/[[:space:]]*$//' -e '/^$/d'
    '''
    runparser(parser, 'kairos')

def bashe():
    parser = '''
    grep 'segment__contant' source/bashe-bashe*.html | grep segment__text__off | cut -d '>' -f 16 | cut -d '<' -f 1 | sort | uniq || true
    '''
    runparser(parser, 'bashe')
