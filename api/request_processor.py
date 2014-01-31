__author__ = 'ernado'

import urllib
import urllib2
import logging
import cookielib

logger = logging.getLogger("cyvk")

def encoded_dict(in_dict):
    out_dict = {}
    for k, v in in_dict.iteritems():
        if isinstance(v, unicode):
            v = v.encode('utf8')
        elif isinstance(v, str):
            # Must be encoded in UTF-8
            v.decode('utf8')
        out_dict[k] = v
    return out_dict

def encode_data(data):
    if not data:
        return None
    data = encoded_dict(data)
    data = urllib.urlencode(data)
    return data

class RequestProcessor(object):
    """
    Processing base requests: POST (multipart/form-data) and GET.
    """
    headers = {"User-agent": "Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:21.0)"
                             " Gecko/20130309 Firefox/21.0",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "Accept-Language": "ru-RU, utf-8"
    }

    def __init__(self):
        self.cookie_jar = cookielib.CookieJar()
        self.cookie_processor = urllib2.HTTPCookieProcessor(self.cookie_jar)
        self.open = urllib2.build_opener(self.cookie_processor).open
        self.open.im_func.func_defaults = (None, 4)

    def get_cookie(self, name):
        for cookie in self.cookie_jar:
            if cookie.name == name:
                return cookie.value

    def request(self, url, data=None, headers=None):
        headers = headers or self.headers
        return urllib2.Request(url, encode_data(data), headers)

    # @attempt_to(5, dict, urllib2.URLError, ssl.SSLError)
    def post(self, url, data=None):
        response = self.open(self.request(url, data or {}))
        body = response.read()
        return body, response

    # @attempt_to(5, dict, urllib2.URLError, ssl.SSLError)
    def get(self, url, query=None):
        if query:
            url += "/?%s" % urllib.urlencode(query)
        response = self.open(self.request(url))
        body = response.read()
        return body, response