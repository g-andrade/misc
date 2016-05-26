import email
import email.Header
import email.Utils
import os
import sys
import re
import time
import signal
import operator
import pickle
from imaplib import IMAP4_SSL
from multiprocessing import Pool

IMAP_FETCH_LIMIT = 1000000000
MAX_IMAP_WORKERS = 5
MAX_LOCAL_WORKERS = 1

def encode_unicode(value):
    # from 'maildir2gmail.py' @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    if isinstance(value, unicode):
        for codec in ['iso-8859-1', 'utf8']:
            try:
                value = value.encode(codec)
                break
            except UnicodeError:
                pass
    return value

def decode_header(value):
    # from 'maildir2gmail.py' @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    result = []
    for v, c in email.Header.decode_header(value):
        try:
            if c is None:
                v = v.decode()
            else:
                v = v.decode(c)
        except (UnicodeError, LookupError):
            v = v.decode('iso-8859-1')
        result.append(v)
    return u' '.join(result)

def parsedate(value):
    # based on 'maildir2gmail.py' @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    try:
        if value:
            value = decode_header(value)
            value = email.Utils.parsedate_tz(value)
            if isinstance(value, tuple):
                timestamp = time.mktime(tuple(value[:9]))
                if value[9]:
                    timestamp -= time.timezone + value[9]
                    if time.daylight:
                        timestamp += 3600
                return time.localtime(timestamp)
    except Exception as e:
        log_error('couldn\'t parse %s as date: %s' % (repr(value), repr(e)))
        return time.localtime(0)

LOGLEVEL_DEBUG = 1
LOGLEVEL_INFO = 2
LOGLEVEL_NOTICE = 3
LOGLEVEL_ERROR = 4
LOGGING_LEVEL = LOGLEVEL_INFO

def log_error(message):
    global LOGLEVEL_ERROR, LOGGING_LEVEL
    if LOGGING_LEVEL <= LOGLEVEL_ERROR:
        log('[ERROR] ' + message)

def log_notice(message):
    global LOGLEVEL_NOTICE, LOGGING_LEVEL
    if LOGGING_LEVEL <= LOGLEVEL_NOTICE:
        log('[notice] ' + message)

def log_info(message):
    global LOGLEVEL_INFO, LOGGING_LEVEL
    if LOGGING_LEVEL <= LOGLEVEL_INFO:
        log('[info] ' + message)

def log_debug(message):
    global LOGLEVEL_DEBUG, LOGGING_LEVEL
    if LOGGING_LEVEL <= LOGLEVEL_DEBUG:
        log('[debug] ' + message)

def log(message):
    # from 'maildir2gmail.py' @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    sys.stderr.write('[%s]: %s\n' % (time.strftime('%H:%M:%S'), encode_unicode(message)))

IMAP_WORKER_OBJ = None
IMAP_WORKER_FOLDER = None
IMAP_WORKER_HOSTNAME = None
IMAP_WORKER_USERNAME = None
IMAP_WORKER_PASSWORD = None
def imap_worker_init(hostname, username, password, folder_name):
    global IMAP_WORKER_OBJ, IMAP_WORKER_FOLDER, IMAP_WORKER_HOSTNAME, IMAP_WORKER_USERNAME, IMAP_WORKER_PASSWORD
    IMAP_WORKER_FOLDER = folder_name
    IMAP_WORKER_HOSTNAME = hostname
    IMAP_WORKER_USERNAME = username
    IMAP_WORKER_PASSWORD = password
    signal.signal(signal.SIGINT, imap_worker_die)
    imap_worker_setup()

def imap_worker_setup():
    global IMAP_WORKER_OBJ, IMAP_WORKER_FOLDER, IMAP_WORKER_HOSTNAME, IMAP_WORKER_USERNAME, IMAP_WORKER_PASSWORD
    if IMAP_WORKER_OBJ is not None:
        try:
            IMAP_WORKER_OBJ.close()
        except Exception as e:
            log_error("couldnt close previous imap connection: %s" % repr(e))
    IMAP_WORKER_OBJ = IMAP4_SSL(IMAP_WORKER_HOSTNAME)
    IMAP_WORKER_OBJ.login(IMAP_WORKER_USERNAME, IMAP_WORKER_PASSWORD)
    log_info('connected \'%s\' to %s' % (IMAP_WORKER_USERNAME, IMAP_WORKER_HOSTNAME))
    IMAP_WORKER_OBJ.select(IMAP_WORKER_FOLDER)

#def imap_worker_die(signum, frame):
def imap_worker_die():
    if IMAP_WORKER_OBJ is not None:
        try:
            IMAP_WORKER_OBJ.close()
        except:
            pass
    sys.exit(-1)

def imap_worker(worker_args):
    global IMAP_WORKER_OBJ
    (func, args) = worker_args
    assert (IMAP_WORKER_OBJ != None)
    val = func(*args)
    return val

def imap_worker_fetch_message_ids(message_refs):
    global IMAP_WORKER_OBJ
    log_info('attempting to fetch %d message ids' % len(message_refs))
    if len(message_refs) < 1:
        return []
    typ, data = IMAP_WORKER_OBJ.fetch(','.join(message_refs), '(BODY[HEADER.FIELDS (MESSAGE-ID)])')
    command_replies = [command_resp for _command, command_resp in data[::2]]
    message_ids = []
    for reply in command_replies:
        if reply[:11].lower() == 'message-id:':
            message_id_parts = filter(len, re.split(r'\s+', reply[11:]))
            if len(message_id_parts) == 1:
                [message_id] = message_id_parts
                if len(message_id) > 0:
                    log_debug('got message id %s' % message_id)
                    message_ids.append(message_id)
                else:
                    log_error('message-id cannot be empty')
            else:
                log_error('unparsable message id: \%s' % message_id_parts)
        else:
            log_error('invalid message-id header: %s' % repr(reply))
    return message_ids

def imap_worker_append_message(filepath, is_dry_sync):
    global IMAP_WORKER_OBJ, IMAP_WORKER_FOLDER
    with open(filepath, 'rb') as msg_file:
        content = msg_file.read()
        message = email.message_from_string(content)
        timestamp = parsedate(message['date'])
        try:
            subject = decode_header(message['subject'])
        except Exception as e:
            log_error('couldn\'t parse %s\'s subject: %s' %
                    (repr(filepath), repr(e)))
            subject = ''
        del message

        log_info('appending \'%s\' (%d bytes)' % (repr(subject), len(content)))
        if not is_dry_sync:
            try:
                IMAP_WORKER_OBJ.append(IMAP_WORKER_FOLDER, '(\\Seen)', timestamp, content)
            except Exception as e:
                log_error('couldn\'t upload %s: %s' % (repr(subject), repr(e)))
                imap_worker_setup()
                return
        return True

def chunks(l, n):
    for i in xrange(0, len(l), n):
        yield l[i:i+n]

def fetch_imap_message_ids(hostname, username, password, folder_name, limit = IMAP_FETCH_LIMIT):
    imap_obj = IMAP4_SSL(hostname)
    imap_obj.login(username, password)
    log_notice('connected \'%s\' to %s' % (username, hostname))
    imap_obj.select(folder_name)
    typ, data = imap_obj.search(None, 'ALL')
    imap_obj.close()
    all_message_refs = data[0].split()
    log_notice('found %d message refs; fetching up to %d message ids' % (
        len(all_message_refs), min(len(all_message_refs), limit)))
    message_refs = all_message_refs[:limit]
    worker_pool = Pool(MAX_IMAP_WORKERS, imap_worker_init, [hostname, username, password, folder_name])
    worker_pool_args = [
            (imap_worker_fetch_message_ids, [chunk])
            for chunk in chunks(message_refs, min(1000, max(1, len(message_refs) // MAX_IMAP_WORKERS)))]
    try:
        message_ids = set(reduce(operator.concat, worker_pool.map(imap_worker, worker_pool_args), []))
        unique = frozenset(message_ids)
        repeated_count = len(message_ids) - len(unique)
        del message_ids
        log_notice('successfully fetched %d message ids (%d repeated)' % (len(unique), repeated_count))
        worker_pool.terminate()
        return unique
    except Exception as e:
        log_error('rage quitting on fetch_imap_message_ids: %s' % repr(e))
        worker_pool.terminate()
        worker_pool.join()
        sys.exit(-1)

def sync(local_file_per_id, remote_ids, hostname, username, password, folder_name, is_dry_sync):
    local_ids = frozenset(local_file_per_id.keys())
    only_local = local_ids - remote_ids
    log_notice('trying to append %d messages' % len(only_local))
    worker_pool = Pool(MAX_IMAP_WORKERS, imap_worker_init, [hostname, username, password, folder_name])
    worker_pool_args = [
            (imap_worker_append_message, [local_file_per_id[message_id], is_dry_sync])
            for message_id in only_local]
    try:
        results = filter(lambda v: v, worker_pool.map(imap_worker, worker_pool_args))
        log_notice('appended %d messages (out of %d)' % (len(results), len(only_local)))
        worker_pool.terminate()
    except Exception as e:
        log_error('rage quitting on sync: %s' % repr(e))
        worker_pool.terminate()
        worker_pool.join()
        sys.exit(-1)


LOCAL_WORKER_CACHE = None
def local_worker_init(cached_id_per_file):
    global LOCAL_WORKER_CACHE
    LOCAL_WORKER_CACHE = cached_id_per_file
    signal.signal(signal.SIGINT, local_worker_die)

def parse_and_append_local_message_id(filepath):
    global LOCAL_WORKER_CACHE
    cached_message_id = LOCAL_WORKER_CACHE.get(filepath)
    if cached_message_id != None:
        return (cached_message_id, filepath)

    with open(filepath, 'rb') as msg_file:
        content = msg_file.read()
        if content.endswith('\x00\x00\x00'):
            log_error('cannot parse %s: corrupted' % repr(os.path.basename(filepath)))
            return
        message = email.message_from_string(content)
        message_id = message['message-id']
        del message
        del content
        if message_id is not None:
            message_id = decode_header(message_id)

        if (message_id is None) or (len(message_id) == 0):
            log_error('cannot sync %s: invalid message id (%s)' % (repr(filepath), repr(message_id)))
        else:
            return (message_id, filepath)

#def imap_worker_die(signum, frame):
def local_worker_die():
    sys.exit(-1)


def fetch_local_message_ids(dirnames):
    global MAX_LOCAL_WORKERS
    filepaths = []
    for dirname in dirnames:
        filenames = os.listdir(dirname)
        log_info('listed %s' % dirname)
        for filename in filenames:
            filepath = os.path.join(dirname, filename)
            if os.path.isfile(filepath):
                filepaths.append(filepath)

    cache_filepath = 'cached_local_message_ids.pickle'
    try:
        with open(cache_filepath, 'rb') as cache_f:
            cached_id_per_file = pickle.load(cache_f)
    except:
        cached_id_per_file = {}

    log_notice('attempting to fetch message ids out of %d files' % len(filepaths))
    worker_pool = Pool(MAX_LOCAL_WORKERS, local_worker_init, [cached_id_per_file])
    try:
        file_per_id_pairs = worker_pool.map(parse_and_append_local_message_id, filepaths)
        defined_file_per_id_pairs = filter(lambda v: v is not None, file_per_id_pairs)
        new_cached_id_per_file = dict([(v, k) for k, v in defined_file_per_id_pairs])
        file_per_id = dict(defined_file_per_id_pairs)

        invalid_count = len(file_per_id_pairs) - len(defined_file_per_id_pairs)
        del file_per_id_pairs
        repeated_count = len(defined_file_per_id_pairs) - len(file_per_id)
        del defined_file_per_id_pairs

        log_notice(
                'successfully fetched %d message ids (%d were invalid, %d were repeated) out of %d files' %
                (len(file_per_id), invalid_count, repeated_count, len(filepaths)))
        worker_pool.terminate()
        with open(cache_filepath, 'wb') as cache_f:
            cached_id_per_file.update(new_cached_id_per_file)
            pickle.dump(cached_id_per_file, cache_f)
        del new_cached_id_per_file
        del cached_id_per_file
        return file_per_id
    except Exception as e:
        log_error("rage quitting on fetch_local_message_ids: %s" % repr(e))
        worker_pool.terminate()
        worker_pool.join()
        sys.exit(-1)

def run(run_type, hostname, username, password, folder_name, dirnames):
    assert (run_type in ['dry', 'dry_sync', 'sync'])
    local_file_per_id = fetch_local_message_ids(dirnames)
    remote_ids = fetch_imap_message_ids(hostname, username, password, folder_name)
    if run_type == 'dry':
        local_ids = frozenset(local_file_per_id.keys())
        only_remote = remote_ids - local_ids
        only_local = local_ids - remote_ids
        common = remote_ids & local_ids
        log_notice('found %d remote-only, %d local-only, %d common IDs' % (
            len(only_remote), len(only_local), len(common)))
    elif run_type == 'dry_sync':
        sync(local_file_per_id, remote_ids, hostname, username, password, folder_name, True)
    elif run_type == 'sync':
        sync(local_file_per_id, remote_ids, hostname, username, password, folder_name, False)

if __name__ == '__main__':
    # Single directory (dry run):
    #   python -OO maildir2imap.py dry imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail' ~/Maildir/cur
    #
    # Single directory (dry sync):
    #   python -OO maildir2imap.py dry_sync imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail' ~/Maildir/cur
    #
    # Single directory (sync):
    #   python -OO maildir2imap.py sync imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail' ~/Maildir/cur
    #
    # Multiple directories (sync):
    #   find ~/Maildir -type d \( -name cur -or -name new \) | sed 's/^\|$/\"/g' | xargs python -OO maildir2imap.py sync imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail'
    #
    run(*(sys.argv[1:6] + [sys.argv[6:]]))
