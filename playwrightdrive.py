#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
loads the dom and fetches html source after javascript rendering w/ playwright (firefox)
used as a fallback to geckodrive.py for sites that fail against selenium
(anti-automation / access-queue challenges playwright's browser fingerprint tends to survive)
'''
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from sharedutils import requiresocks
from sharedutils import randomagent
from sharedutils import sockshost, socksport
from sharedutils import stdlog, dbglog, errlog

def main(webpage):
    stdlog('playwrightdrive: ' + 'starting to fetch ' + webpage)
    proxy = None
    if '.onion' in webpage:
        stdlog('playwrightdrive: ' + 'appears we are dealing with an onionsite')
        requiresocks('playwrightdrive: ')
        proxy = {'server': 'socks5://' + sockshost + ':' + str(socksport)}
        dbglog('playwrightdrive: ' + 'configured proxy - ' + proxy['server'])
    source = None
    try:
        with sync_playwright() as playwright:
            stdlog('playwrightdrive: ' + 'starting browser')
            browser = playwright.firefox.launch(headless=True, proxy=proxy)
            context = browser.new_context(
                user_agent=randomagent(),
                ignore_https_errors=True
            )
            page = context.new_page()
            page.set_default_navigation_timeout(20000)
            page.set_default_timeout(20000)
            try:
                stdlog('playwrightdrive: ' + 'fetching webpage')
                page.goto(webpage, wait_until='domcontentloaded')
                page.wait_for_timeout(5000)
                source = page.content()
            except PlaywrightTimeoutError as pte:
                errlog('playwrightdrive: ' + 'page load timeout reached - ' + str(pte))
            except PlaywrightError as pe:
                errlog('playwrightdrive: ' + 'unknown error during page load: ' + str(pe))
            finally:
                context.close()
                browser.close()
                stdlog('playwrightdrive: ' + 'browser closed')
    except PlaywrightError as pe:
        errlog('playwrightdrive: ' + 'error: ' + str(pe))
        return None
    return source
