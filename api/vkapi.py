from __future__ import unicode_literals
import logging
import json

try:
    from urllib2 import URLError
except ImportError:
    from urllib.error import URLError

from api import webtools

import database
from friends import get_friend_jid
from api.request_processor import RequestProcessor
from errors import AuthenticationException, CaptchaNeeded, NotAllowed, APIError, AccessRevokedError, InvalidTokenError
from messaging.parsing import escape_name
from parallel import realtime
from parallel.sending import send
from compatibility import text_type
import time
from config import MAX_API_RETRY


logger = logging.getLogger("cyvk")

from config import APP_ID, APP_SCOPE, API_MAXIMUM_RATE, TRANSPORT_ID


VK_ERROR_BURST = 6


def method(method_name, jid, args=None, additional_timeout=0, retry=0, token=None):
    """
    Makes post-request to vk api witch burst protection and exception handling
    @type jid: text_type
    @type method_name: text_type
    @param method_name: vk api method name
    @param jid: client jid
    @param args: method parameters
    @param additional_timeout: time in seconds to wait before reattempting
    @return: @raise NotImplementedError:
    """


    assert isinstance(method_name, text_type)
    assert isinstance(jid, text_type)

    if retry > MAX_API_RETRY:
        logging.error('reached max api retry for %s, %s' % (method_name, jid))


    args = args or {}
    url = 'https://api.vk.com/method/%s' % method_name

    if not token:
        token = realtime.get_token(jid)

    if not token:
        raise ValueError('no token for %s' % jid)

    args['access_token'] = token
    args["v"] = '3.0'

    logger.debug('api method %s, arguments: %s' % (method_name, args))

    #
    # interval = API_MAXIMUM_RATE - (time.time() - realtime.get_last_method_time(jid))
    #
    # # no blocking for callbacks
    # if callback and (interval > 0):
    #     t = threading.Timer(interval, method, (method_name, jid, args, additional_timeout, retry, callback))
    #     return t.start()

    if additional_timeout:
        time.sleep(additional_timeout)

    realtime.wait_for_api_call(jid)

    rp = RequestProcessor()

    try:
        response = rp.post(url, args)
    except URLError as e:
        logger.debug('method error: %s' % e)

        if not additional_timeout:
            additional_timeout = 1

        additional_timeout*=2

        return method(method_name, jid, args, additional_timeout, retry)

    if not response:
        logger.debug('no response')
        raise URLError('no response')

    body, response = response

    if not body:
        raise RuntimeError('got blank body')

    body = json.loads(body)

    if 'response' in body:
        return body['response']

    if 'error' in body:
        code = body['error']['error_code']

        if code == VK_ERROR_BURST:
            logger.debug('too many requests per second, trying again')
            if additional_timeout:
                additional_timeout *= 2
            else:
                additional_timeout = API_MAXIMUM_RATE
            return method(method_name, jid, args, additional_timeout, retry+1)


    raise NotImplementedError('unable to process %s' %  body)


class APIBinding:
    def __init__(self, token, password=None, app_id=APP_ID, scope=APP_SCOPE):
        assert token is not None
        logger.debug('api bindings initialized with token %s' % token)
        self.password = password
        # self.number = number

        self.sid = None
        self.token = token
        self.captcha = {}
        self.last = []
        self.last_method = None

        self.app_id = app_id
        self.scope = scope

        self.rp = RequestProcessor()
        self.attempts = 0


    def check_sid(self):
        logger.debug('VKAPI check_sid')

        if self.sid:
            url = "https://vk.com/feed2.php"
            get = self.rp.get(url)
            body, response = get
            if body and response:
                data = json.loads(body)
                if data["user"]["id"] != -1:
                    return data


def method_wrapped(m, jid, args=None, token=None):
    """

    @type jid: text_type
    @param jid: client jid
    @param m: method name
    @param args: method arguments
    @return: @raise NotImplementedError:
    """
    args = args or {}

    assert isinstance(jid, text_type)
    assert isinstance(m, text_type)
    assert isinstance(args, dict)

    result = {}

    # TODO: Captcha too

    logger.debug('wrapped %s, args=%s, t=%s' % (m, args, token))

    try:
        result = method(m, jid, args, token=token)
    except CaptchaNeeded:
        logger.error("VKLogin: running captcha challenge for %s" % jid)
        # TODO: Captcha
        raise NotImplementedError('Captcha')
    except NotAllowed:
        # if self.engine.lastMethod[0] == "messages.send":
        # TODO: replace
        send(jid, "You're not allowed to perform this action.",
                get_friend_jid(args.get("user_id", TRANSPORT_ID)))
    except AccessRevokedError:
        logger.debug('user %s revoked access' % jid)
        database.remove_user(jid)
        realtime.remove_online_user(jid)
    except InvalidTokenError:
        # TODO: replace
        send(jid, 'Your token is invalid. Please, register again', TRANSPORT_ID)

    return result


def is_application_user(jid, token):
    """
    Check if client is application user and validate token
    @type jid: text_type
    @param jid: client jid
    @return:
    """
    logger.debug('login api: checking token')

    assert isinstance(jid, text_type)

    try:
        method_wrapped('isAppUser', jid, token=token)
        logger.debug('token for %s is valid' % jid)
        return True
    except AuthenticationException as auth_e:
        logger.debug('checking token failed: %s' % auth_e)
        return False


def mark_messages_as_read(jid, msg_list):
    # TODO: can be asyncronous call
    method("messages.markAsRead", jid, {"message_ids": ','.join(msg_list)})


def get_messages(jid, count=5, last_msg_id=None):
    logger.debug('getting messages for %s' % jid)
    arguments = {"out": 0, "filters": 1, "count": count}
    if last_msg_id:
        arguments.update({'last_message_id': last_msg_id})
    else:
        arguments.update({'count': count})
    return method("messages.get", jid, arguments)


def get_user_data(uid, target_uid, fields=None):
    logger.debug('user api: sending user data for %s about %s' % (uid, target_uid))
    fields = fields or ["screen_name"]
    args = {"fields": ",".join(fields), "user_ids": target_uid}
    m = "users.get"
    data = method(m, uid, args)

    if data:
        data = data[0]
        data["name"] = escape_name("", u"%s %s" % (data["first_name"], data["last_name"]))
        del data["first_name"], data["last_name"]
    else:
        data = {}
        for key in fields:
            data[key] = "Unknown error when trying to get user data. We're so sorry."
    return data