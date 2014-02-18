from __future__ import unicode_literals

import time
import logging

from parallel.stanzas import push
from parallel.updates import send_messages, send_message, get_friends

from config import TRANSPORT_ID, IDENTIFIER
from database import set_token
from friends import get_friend_jid

from parallel import realtime
from parallel.long_polling import start_polling

import xmpp as xmpp
from errors import CaptchaNeeded, InvalidTokenError, AuthenticationException
from api.vkapi import is_application_user

import database


logger = logging.getLogger("cyvk")


def send_presence(target, jid_from, presence_type=None, nick=None, reason=None):
    logger.debug('sending presence for %s about %s' % (target, jid_from))
    presence = xmpp.Presence(target, presence_type, frm=jid_from, status=reason)
    if nick:
        presence.setTag("nick", namespace=xmpp.NS_NICK)
        presence.setTagData("nick", nick)
    push(presence)
    # gateway.send(presence)


def roster_subscribe(jid, subscriptions=None):
    """
    Subscribe user for jids in dist
    """
    if subscriptions:
        logger.debug('roster_subscribe for %s: %s' % (jid, subscriptions.keys()))
    else:
        logger.debug('roster_subscribe for transport')

    if not subscriptions:
        send_presence(jid, TRANSPORT_ID, "subscribe", IDENTIFIER["name"])
        return

    for uid, value in subscriptions.iteritems():
        send_presence(jid, get_friend_jid(uid), "subscribe", value["name"])


def send_init_presence(jid):
    """
    Sends initial presences to user about friends and transport
    @type jid: unicode
    @param jid: user jid
    @return: None
    """
    assert isinstance(jid, unicode)
    friends = realtime.get_friends(jid)
    assert isinstance(friends, dict)
    online_friends = filter(lambda uid: friends[uid]['online'], friends)
    logger.debug('user api: sending initial status to %s, with friends: %s' % (jid, online_friends != {}))

    for friend_uid in online_friends:
        send_presence(jid, get_friend_jid(friend_uid), nick=friends[friend_uid]['name'])

    # sending transport presence
    send_presence(jid, TRANSPORT_ID, nick=IDENTIFIER["name"])


def send_out_presence(jid, reason=None):
    assert isinstance(jid, unicode)

    status = "unavailable"
    logger.debug("user api: sending out presence for %s" % jid)
    notification_list = realtime.get_friends(jid).keys() + [TRANSPORT_ID]

    for uid in notification_list:
        send_presence(jid, get_friend_jid(uid), status, reason=reason)


def delete_user(jid):
    assert isinstance(jid, unicode)

    logger.debug("user api: delete_user %s" % jid)

    friends = realtime.get_friends(jid)

    for friend_id in friends:
        friend_jid = get_friend_jid(friend_id)
        send_presence(jid, friend_jid, "unsubscribe")
        send_presence(jid, friend_jid, "unsubscribed")

    database.remove_user(jid)
    realtime.remove_online_user(jid)


def update_friends(jid):
    friends_vk = get_friends(jid)
    friends_db = realtime.get_friends(jid)

    assert isinstance(jid, unicode)
    assert isinstance(friends_vk, dict)
    assert isinstance(friends_db, dict)

    if friends_db == friends_vk:
        logger.debug('no changes in friend list for %s' % jid)
        return

    logger.debug('updating friend list for %s' % jid)

    subscriptions = {}
    update_status_dict = {}

    for uid in friends_vk:
        friend = friends_vk[uid]

        if uid not in friends_db:
            logger.debug('friend %s not found' % uid)
            subscriptions.update({uid: friend})
            continue

        if friends_db[uid]['online'] != friend['online']:
            logger.debug('friend %s status changed' % uid)
            status = None if friend["online"] else "unavailable"
            update_status_dict.update({uid: status})

    roster_subscribe(jid, subscriptions)

    for uid, status in update_status_dict.items():
        send_presence(jid, get_friend_jid(uid), status)

    realtime.set_friends(jid, friends_vk)


def initialize(jid, send_precense=True):
    """
    Initializes user by subscribing to friends and sending initial presence
    @type jid: unicode
    @param jid: client jid
    @param send_precense: send presence flag
    """
    logger.debug("user api: called init for user %s" % jid)

    assert isinstance(jid, unicode)

    # getting friends from vk api
    friends = get_friends(jid)

    # updating user in redis
    realtime.set_friends(jid, friends)
    realtime.unset_polling(jid)
    realtime.unset_processing(jid)

    if friends:
        logger.debug("user api: subscribing friends for %s" % jid)
        roster_subscribe(jid, friends)

    roster_subscribe(jid)   # subscribing to transport

    if send_precense:
        logger.debug('sending initial presence')
        send_init_presence(jid)


def load(jid):
    logger.debug("user api: loading %s" % jid)
    desc = database.get_description(jid)

    if not desc:
        raise ValueError('user api: user not found %s' % jid)

    logger.debug("user api: %s exists in db" % jid)
    jid = desc['jid']

    realtime.set_last_message(jid, desc['last_message_id'])
    if desc['roster_set_flag']:
        realtime.set_roster_flag(jid)

    realtime.set_friends(jid, {})

    logger.debug("user api: %s data loaded" % jid)


def connect(jid, token):
    logger.debug("user api: connecting %s" % jid)

    if not token:
        raise AuthenticationException('no token for %s' % jid)

    # logger.debug("user api: vk api initialized")
    try:
        logger.debug('user api: trying to auth with token')
        is_application_user(jid, token)
        set_token(jid, token)
        logger.debug("user api: authenticated %s" % jid)
    except CaptchaNeeded:
        logger.debug("user api: captcha needed for %s" % jid)
        # roster_subscribe(jid)
        raise NotImplementedError('Captcha')
        # self.vk.captcha_challenge()
        # return True
    except InvalidTokenError as token_error:
        # TODO: Replace by exception handling
        if token_error.message == "User authorization failed: user revoke access for this token.":
            logger.critical("user api: %s" % token_error.message)
            delete_user(jid)
        elif token_error.message == "User authorization failed: invalid access_token.":
            send_message(jid, token_error.message + " Please, register again", TRANSPORT_ID)
        raise AuthenticationException('invalid token')

    if realtime.is_user(jid):
        logger.debug("user api: updating db for %s" % jid)
        realtime.set_last_activity_now(jid)


def process_client(jid):
    """
    Updates client messages, friends and status
    @type jid: unicode
    @param jid: client jid
    @return:
    """
    assert isinstance(jid, unicode)

    if realtime.is_processing(jid):
        logger.debug('already processing client %s' % jid)
        return

    realtime.set_processing(jid)

    if not realtime.is_polling(jid):
        update_friends(jid)
        send_messages(jid)
        start_polling(jid)
    else:
        logger.debug('updates for %s are handled by polling' % jid)

    realtime.unset_processing(jid)


def update_transports_list(jid, add=True):
    is_client = realtime.is_client(jid)
    if not is_client:
        if add:
            realtime.add_online_user(jid)
        else:
            realtime.remove_online_user(jid)

    process_client(jid)


def process_users():
    now = time.time()
    clients = realtime.get_clients()

    if not clients:
        logger.debug('no clients')
        return

    l = len(map(process_client, clients))

    logger.debug('iterated for %.2f ms - %s users' % ((time.time() - now) * 1000, l))


def add_client(jid):
    assert isinstance(jid, unicode)

    logger.debug('add_client %s' % jid)

    if realtime.is_client(jid):
        logger.debug('%s already a client' % jid)
        return

    realtime.add_online_user(jid)
    process_client(jid)