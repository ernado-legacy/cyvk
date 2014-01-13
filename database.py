import os
import redis
import time
from library.itypes import Database

import logging
from config import DB_FILE, TRANSPORT_ID, REDIS_PREFIX, REDIS_HOST, REDIS_PORT, USE_LAST_MESSAGE_ID
import library.xmpp as xmpp
import json


logger = logging.getLogger("vk4xmpp")


r = redis.StrictRedis(REDIS_HOST, REDIS_PORT, )

def init_db(filename):
    logger.info('DB: initializing')
    if not os.path.exists(filename):
        with Database(filename) as db:
            db("CREATE TABLE users (jid TEXT, username TEXT, token TEXT, lastMsgID INTEGER, rosterSet bool)")
            db.commit()
    logger.info('DB: ok')
    return True

def remove_user(jid):
    logger.debug('DB: removing %s' % jid)
    with Database(DB_FILE) as db:
        db("DELETE FROM users WHERE jid=?", (jid,))
        db.commit()
        logger.debug('DB: removed %s' % jid)

def init_users(gateway):
    logger.info('DB: Initializing users')
    with Database(DB_FILE) as db:
        users = db("SELECT * FROM users").fetchall()

        for user in users:
            logger.debug('DB: user %s initialized' % user[0])
            gateway.send(xmpp.Presence(user[0], "probe", frm=TRANSPORT_ID))

def _get_last_message_key(jid):
    return _get_user_attribute_key(jid, 'last_message')

def set_last_message(message_id, jid):
    if not USE_LAST_MESSAGE_ID:
        return None

    logger.debug('DB: setting last message %s for %s' % (message_id, jid))

    r.set(_get_last_message_key(jid), message_id)

    with Database(DB_FILE) as db:
        db("UPDATE users SET lastMsgID=? WHERE jid=?", (message_id, jid))


def get_last_message(jid):

    if not USE_LAST_MESSAGE_ID:
        return None

    try:
        return int(r.get(_get_last_message_key(jid)))
    except ValueError as e:
        logger.error('get_last_message for %s error: %s' % (jid, e))
        raise e


def roster_subscribe(roster_set, jid):
    # logger.debug('DB: subscribing %s for %s' % (roster_set, jid))
    with Database(DB_FILE) as db:
        db("UPDATE users SET rosterSet=? WHERE jid=?", (roster_set, jid))


USER_PREFIX = 'user'
TOKEN_PREFIX = 'token'
ACTIVITY = 'activity'

def add_user(jid, username, token, last_msg_id, roster_set):
    logger.debug('DB: adding user %s' % jid)
    with Database(DB_FILE) as db:
        db("INSERT INTO users VALUES (?,?,?,?,?)", (jid, username,
                                                    token, last_msg_id, roster_set))

def get_description(jid):
    with Database(DB_FILE) as db:
        db("SELECT * FROM users WHERE jid=?", (jid,))
        return db.fetchone()

def _get_user_attribute_key(user, attribute):
    return ':'.join([REDIS_PREFIX, USER_PREFIX, user, attribute])

def _get_token_key(user):
    return _get_user_attribute_key(user, TOKEN_PREFIX)

def set_token(user, token):
    r.set(_get_token_key(user), token)
    with Database(DB_FILE) as db:
        db("UPDATE users SET token=? WHERE jid=?", (token, user))

def get_token(user):
    return r.get(_get_token_key(user))

def set_last_activity(user, time):
    k = _get_user_attribute_key(user, ACTIVITY)
    r.set(k, time)

BURST_RATE = 0.3

burst_protection_key = ':'.join([REDIS_PREFIX, 'burst'])

clients_set_key = ':'.join([REDIS_PREFIX, 'clients'])

def set_burst():
    r.set(burst_protection_key, time.time())

def is_client(jid):
    return r.sismember(clients_set_key, jid)


def burst_protection():
    """
    Waits until there is BURST_RATE seconds between api calls
    """

    now = time.time()
    last_time = float(r.get(burst_protection_key))

    diff = now - last_time
    if diff < BURST_RATE:
        logger.debug('Burst protection succeeded')
        time.sleep(abs(diff - BURST_RATE))

    set_burst()

def _get_friends_key(uid):
    return _get_user_attribute_key(uid, 'friends')

def _get_status_key(uid):
    return _get_user_attribute_key(uid, 'status')


def get_friends(uid):
    try:
        return json.loads(r.get(_get_friends_key(uid)))
    except TypeError:
        return {}

STATUS_ONLINE = 'online'
STATUS_OFFLINE = 'offline'
ONLINE_TIMEOUT = 60

def set_online(uid):
    key = _get_status_key(uid)
    r.set(key, STATUS_ONLINE)
    r.expire(key, ONLINE_TIMEOUT)


def set_offline(uid):
    key = _get_status_key(uid)
    r.set(key, STATUS_OFFLINE)

def is_user_online(uid):
    return r.get(_get_status_key(uid)) == STATUS_ONLINE


def set_friends(uid, friends):
    friends_json = json.dumps(friends)
    r.set(_get_friends_key(uid), friends_json)

def get_last_activity(user):
    k = _get_user_attribute_key(user, ACTIVITY)
    try:
        return float(r.get(k))
    except ValueError:
        return 0


LAST_UPDATE = 'last_update'

def _get_last_update_key(uid):
    return _get_user_attribute_key(uid, LAST_UPDATE)

def get_last_update(uid):
    last_update = r.get(_get_last_update_key(uid))
    try:
        return float(last_update)
    except TypeError:
        return 0

def set_last_update_now(uid):
    last_update = time.time()
    r.set(_get_last_update_key(uid), last_update)

def get_users():
    return r.smembers(clients_set_key)

def reset_online_users():
    r.delete(clients_set_key)

def add_online_user(jid):
    r.sadd(clients_set_key, jid)

def remove_online_user(jid):
    r.srem(clients_set_key, jid)


