import logging

from config import WATCHER_LIST, TRANSPORT_ID
from parallel.stanzas import push
from transport.messages import get_message_stanza
from transport.statuses import get_typing_stanza


logger = logging.getLogger("cyvk")


def send(jid_to, body, jid_from, timestamp=None):
    logger.debug('sending message %s -> %s' % (jid_from, jid_to))

    assert isinstance(jid_to, unicode)
    assert isinstance(jid_from, unicode)
    assert isinstance(body, unicode)

    message = get_message_stanza(jid_to, body, jid_from, timestamp)

    push(message)


def send_typing_status(jid_to, jid_from):
    logger.debug('typing %s -> %s' % (jid_from, jid_to))

    assert isinstance(jid_to, unicode)
    assert isinstance(jid_from, unicode)

    message = get_typing_stanza(jid_to, jid_from)

    push(message)


def send_to_watcher(text):
    """
    Send message to watcher
    @type text: unicode
    @param text: unicode message body
    """

    assert isinstance(text, unicode)

    logger.debug('sending message %s to watchers' % text)

    for jid in WATCHER_LIST:
        send(jid, text, TRANSPORT_ID)