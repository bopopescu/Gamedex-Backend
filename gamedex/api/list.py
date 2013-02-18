"""list.py: API for retrieving list data """

__author__ = "Michael Martin"
__status__ = "Production"


import datetime
import time
import logging
import json

from google.appengine.api import urlfetch
from google.appengine.api import memcache

from django.http import HttpResponse
from lxml.cssselect import CSSSelector
from lxml import etree
from boto.s3.connection import S3Connection

from management.keys import Keys

# constants
# base url
S3_URL = 'https://s3.amazonaws.com/'
S3_ACCESS_KEY = 'AMAZON_ACCESS_KEY'
S3_SECRET_KEY = 'AMAZON_SECRET_KEY'

# site bucket
UPCOMING_LIST_BUCKET = 's3.gamedex.net-upcominglist'
RELEASED_LIST_BUCKET = 's3.gamedex.net-releasedlist'
REVIEWED_LIST_BUCKET = 's3.gamedex.net-reviewedlist'

# S3 Properties
AWS_HEADERS = {
    'Cache-Control': 'max-age=2592000,public'
}
AWS_ACL = 'public-read'

# ign
IGN_UPCOMING_URL = 'http://www.ign.com/games/upcoming-ajax'
IGN_REVIEWED_URL = 'http://www.ign.com/games/reviews-ajax'
IGN_ITEMS_PER_PAGE = 25

# gamestats
GAMESTATS_BASE_URL = 'http://www.gamestats.com/index/gpm/'

# gametrailers
GT_BASE_URL = 'http://www.gametrailers.com/release_calendar_ajax/'
GT_PROMOTION_ID = 'ae8bcc1b-d7f8-4b5b-8854-fd2700a56990'


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# POPULAR LIST (GAMESTATS)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def popularList(request):

    # http://www.gamestats.com/index/gpm/xbox-360.html
    url = GAMESTATS_BASE_URL
    platform = 'all'

    if 'platform' in request.GET and request.GET.get('platform') != '':
        platform = request.GET.get('platform')
        url = GAMESTATS_BASE_URL + platform + '.html'

    # return memcached list if available
    gameStatsByGPM = memcache.get('gameStatsListByGPM_' + platform)
    if gameStatsByGPM is not None:
        return HttpResponse(json.dumps(gameStatsByGPM), mimetype='application/json')

    # load list from source
    else:

        # fetch(url, payload=None, method=GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=None, validate_certificate=None)
        # allow 30 seconds for response
        response = urlfetch.fetch(url, None, 'GET', {}, False, False, 30)

        if response.status_code == 200:

            # parse game stats list result
            gameStatsList = parsePopularList(response.content, platform)

            # cache game stats list for 1 day
            if not memcache.add('gameStatsListByGPM_' + platform, gameStatsList, 86400):
                logging.error('gameStatsListByGPM: Memcache set failed')

            return HttpResponse(json.dumps(gameStatsList), mimetype='application/json')


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# PARSE POPULAR LIST (GAMESTATS)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def parsePopularList(response, platform):

    list = []

    try:
        html = etree.HTML(response)

        # select all tr from 3rd table
        rowSel = CSSSelector('table:nth-child(3) tr')

        # all platform has only 1 table
        if (platform == 'all'):
            rowSel = CSSSelector('table tr')

        # select all 2nd tds
        tdSel = CSSSelector('td:nth-child(2) a')

        for row in rowSel(html):

            try:
                # get 2nd td elements from row
                element = tdSel(row)

                name = element[0].text.strip()
                url = element[0].get('href')

                if (name != 'Next 50'):
                    listObj = {'name': name, 'gameStatsPage': url}
                    list.append(listObj)

            except IndexError:
                logging.error('parsePopularList: IndexError')

    except:
        logging.error('parsePopularList: Parse Error')

    # return list
    return list


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# REVIEWED LIST (IGN)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def ignReviewedList(request):

    if 'platform' in request.GET:
        platform = request.GET.get('platform')

    if 'page' in request.GET:
        page = request.GET.get('page')

    memcacheKey = 'ignReviewedList_' + platform + '_' + page

    # return memcached list if available
    result = memcache.get(memcacheKey)
    if result is not None:
        return HttpResponse(json.dumps(result), mimetype='application/json')

    # load list from source
    else:

        # offset=0
        offset = IGN_ITEMS_PER_PAGE * int(page)
        url = IGN_REVIEWED_URL + '?startIndex=' + str(offset) + '&platformSlug=' + platform

        # fetch(url, payload=None, method=GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=None, validate_certificate=None)
        # allow 30 seconds for response
        response = urlfetch.fetch(url, None, 'GET', {}, False, False, 30)

        if response.status_code == 200:

            # parse result
            result = parseIGNReviewedList(response.content)

            # cache game stats list for 1 day
            if not memcache.add(memcacheKey, result, 86400):
                logging.error('ignReviewedList: Memcache set failed')

            return HttpResponse(json.dumps(result), mimetype='application/json')


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# PARSE REVIEWED LIST (IGN)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def parseIGNReviewedList(response):

    list = []
    titleIndex = {}

    # s3 connection
    s3conn = S3Connection(Keys.getKey(S3_ACCESS_KEY), Keys.getKey(S3_SECRET_KEY), is_secure=False)

    html = etree.HTML(response)

    rowSel = CSSSelector('.itemList-item')
    nameSel = CSSSelector('.item-title a')
    imageSel = CSSSelector('.grid_3.alpha img')
    dateSel = CSSSelector('.grid_3:nth-child(3) div')

    for row in rowSel(html):

        try:
            nameElement = nameSel(row)
            imageElement = imageSel(row)
            dateElement = dateSel(row)

            name = nameElement[0].text.strip()
            url = nameElement[0].get('href').strip()
            imageURL = imageElement[0].get('src').strip()
            date = dateElement[0].text.strip()
            displayDate = dateElement[0].text.strip()

            # check if title name already added to list
            if (name not in titleIndex):

                # copy IGN image to S3 bucket
                # get filename and extension
                filename = imageURL.split('/')[-1]
                extension = filename.split('.')[-1]
                image = copyImageToS3(REVIEWED_LIST_BUCKET, imageURL, filename, extension, s3conn)

                listObj = {'name': name, 'IGNPage': url, 'calendarDate': displayDate, 'releaseDate': date, 'mediumImage': image}
                list.append(listObj)

                # add to title index
                titleIndex[name] = True

        except IndexError:
            logging.error('parseIGNReviewedList: IndexError')

    # return list
    return list


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# UPCOMING LIST (IGN)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def ignUpcomingList(request):

    if 'platform' in request.GET:
        platform = request.GET.get('platform')

    if 'page' in request.GET:
        page = request.GET.get('page')

    memcacheKey = 'ignUpcomingList_' + platform + '_' + page

    # return memcached list if available
    result = memcache.get(memcacheKey)
    if result is not None:
        return HttpResponse(json.dumps(result), mimetype='application/json')

    # load list from source
    else:

        # offset=0
        offset = IGN_ITEMS_PER_PAGE * int(page)
        url = IGN_UPCOMING_URL + '?startIndex=' + str(offset) + '&platformSlug=' + platform

        logging.info(url)

        # fetch(url, payload=None, method=GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=None, validate_certificate=None)
        # allow 30 seconds for response
        response = urlfetch.fetch(url, None, 'GET', {}, False, False, 30)

        logging.info(response.status_code)
        logging.info(response.content)

        if response.status_code == 200:

            # parse result
            result = parseIGNUpcomingList(response.content)

            # cache game stats list for 1 day
            if not memcache.add(memcacheKey, result, 86400):
                logging.error('ignReviewedList: Memcache set failed')

            return HttpResponse(json.dumps(result), mimetype='application/json')


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# PARSE UPCOMING LIST (IGN)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def parseIGNUpcomingList(response):

    list = []
    titleIndex = {}

    # s3 connection
    s3conn = S3Connection(Keys.getKey(S3_ACCESS_KEY), Keys.getKey(S3_SECRET_KEY), is_secure=False)

    html = etree.HTML(response)

    rowSel = CSSSelector('.itemList-item')
    nameSel = CSSSelector('.item-title a')
    imageSel = CSSSelector('.grid_3.alpha img')
    dateSel = CSSSelector('.releaseDate')

    for row in rowSel(html):

        try:
            nameElement = nameSel(row)
            imageElement = imageSel(row)
            dateElement = dateSel(row)

            name = nameElement[0].text.strip()
            url = nameElement[0].get('href').strip()
            imageURL = imageElement[0].get('src').strip()
            date = dateElement[0].text.strip()
            displayDate = dateElement[0].text.strip()

            # check if title name already added to list
            if (name not in titleIndex):

                # copy IGN image to S3 bucket
                # get filename and extension
                filename = imageURL.split('/')[-1]
                extension = filename.split('.')[-1]
                image = copyImageToS3(UPCOMING_LIST_BUCKET, imageURL, filename, extension, s3conn)

                # detect TBA 20XX - signifies unknown date > change to real date: Dec 31, 20XX
                dateParts = date.split(' ')
                if (dateParts[0] == 'TBA'):
                    date = 'Dec 31, ' + dateParts[1]

                listObj = {'name': name, 'IGNPage': url, 'calendarDate': displayDate, 'releaseDate': date, 'mediumImage': image}
                list.append(listObj)

                # add to title index
                titleIndex[name] = True

        except IndexError:
            logging.error('parseIGNUpcomingList: IndexError')

    # return list
    return list


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# RELEASED LIST (GT)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def gtReleasedList(request):

    if all(k in request.GET for k in ('year', 'month', 'day')):

        # get parameters
        year = request.GET.get('year')
        month = request.GET.get('month')
        day = request.GET.get('day')

        memcacheKey = 'gtReleasedList_' + year + '_' + month + '_' + day

        # return memcached list if available
        result = memcache.get(memcacheKey)
        if result is not None:
            return HttpResponse(json.dumps(result), mimetype='application/json')

        # load list from source
        else:

            # 1341091189076 datetime.datetime.now() * 1000 - now timestamp in milliseconds
            # promotionID
            # 1339300800 - unix time (Sun, 10 Jun 2012 04:00:00 GMT) - week to fetch

            # /ae8bcc1b-d7f8-4b5b-8854-fd2700a56990/1339300800/
            # now stamp

            # week stamp
            displayDate = datetime.datetime(int(year), int(month), int(day), 6, 0, 0)
            # unix timestamp
            displayStamp = int(time.mktime(displayDate.utctimetuple()))

            url = GT_BASE_URL + GT_PROMOTION_ID + '/' + str(displayStamp)

            logging.info('------------------------')
            logging.info('------------------------')
            logging.info(url)
            logging.info('------------------------')
            logging.info('------------------------')

            # fetch(url, payload=None, method=GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=None, validate_certificate=None)
            # allow 30 seconds for response
            response = urlfetch.fetch(url, None, 'GET', {}, False, False, 30)

            if response.status_code == 200:

                # parse game stats list result
                result = parseGTReleasedList(response.content)

                # cache game stats list for 30 days
                if not memcache.add(memcacheKey, result, 2592000):
                    logging.error('gtReleasedList: Memcache set failed')

                return HttpResponse(json.dumps(result), mimetype='application/json')

    else:
        return HttpResponse('missing_param', mimetype='text/plain', status='500')


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# PARSE RELEASED LIST (GT)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def parseGTReleasedList(response):

    list = []

    # s3 connection
    s3conn = S3Connection(Keys.getKey(S3_ACCESS_KEY), Keys.getKey(S3_SECRET_KEY), is_secure=False)

    html = etree.HTML(response)

    rowSel = CSSSelector('.week li')
    urlSel = CSSSelector('h3 a')
    imageSel = CSSSelector('h3 a img')
    dateSel = CSSSelector('p span')
    platformsSel = CSSSelector('.platforms span')

    for row in rowSel(html):

        try:
            urlElement = urlSel(row)
            imageElement = imageSel(row)
            dateElement = dateSel(row)
            platformsElement = platformsSel(row)

            url = urlElement[0].get('href').strip()
            imageURL = imageElement[0].get('src').strip()
            name = imageElement[0].get('alt').strip()
            date = dateElement[0].text.strip()
            platforms = platformsElement[0].text.strip()

            # update imageURL width
            imageURL = imageURL.split('?')[0] + '?width=120'

            #image = copyImageToS3(image, s3conn)
            if (name != None and name != ''):

                # get filename and extension
                filename = imageURL.split('/')[-1].split('?')[0]
                extension = filename.split('.')[-1].split('?')[0]

                # copy GT image to S3 bucket
                image = copyImageToS3(RELEASED_LIST_BUCKET, imageURL, filename, extension, s3conn)
                listObj = {'name': name, 'GTPage': url, 'calendarDate': date, 'releaseDate': date, 'mediumImage': image, 'platforms': platforms}

                # append to output list
                list.append(listObj)

        except IndexError:
            logging.error('parseGTReleasedList: IndexError')

    # return list
    return list


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# COPY LIST IMAGE
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def copyImageToS3(S3bucket, url, filename, extension, s3conn):

    # get s3 bucket
    bucket = s3conn.get_bucket(S3bucket, validate=False)

    # load url
    response = urlfetch.fetch(url, None, 'GET', {}, False, False, 30)

    # create new S3 key, set mimetype and Expires header
    k = bucket.new_key(filename)
    if (extension.lower() == 'jpg' or extension.lower() == 'jpeg'):
        mimeType = 'jpeg'
    elif (extension.lower() == 'png'):
        mimeType = 'png'
    elif (extension.lower() == 'gif'):
        mimeType = 'gif'
    else:
        mimeType = 'jpeg'

    k.content_type = 'image/' + mimeType

    # write file from response string set public read permission
    k.set_contents_from_string(response.content, headers=AWS_HEADERS, replace=False, policy=AWS_ACL)
    k.set_acl('public-read')

    # s3 url
    return S3_URL + S3bucket + '/' + filename
