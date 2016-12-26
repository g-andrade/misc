#!/usr/bin/env python
import email
import email.Header
import email.Utils
import getpass
from imaplib import IMAP4_SSL
from multiprocessing import Pool
import operator
import os
import pickle
import random
import signal
import string
import sys
import re
import time
import traceback
import unicodedata

IMAP_FETCH_LIMIT = 10 ** 15
MAX_IMAP_WORKERS = 5
MAX_LOCAL_WORKERS = 5
TEMP_PREFIX = u'._'

def encode_unicode(value):
    # from 'maildir2gmail.py'
    # @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    if isinstance(value, unicode):
        for codec in ['iso-8859-1', 'utf8']:
            try:
                value = value.encode(codec)
                break
            except UnicodeError:
                pass
    return value

def decode_header(value):
    # from 'maildir2gmail.py'
    # @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
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

def parse_date_header(value):
    # based on 'maildir2gmail.py'
    # @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    try:
        if value:
            value = decode_header(value)
            value = email.utils.parsedate_tz(value)
            if isinstance(value, tuple):
                timestamp = time.mktime(tuple(value[:9]))
                if value[9]:
                    timestamp -= time.timezone + value[9]
                    if time.daylight:
                        timestamp += 3600
                return timestamp
    except Exception as e:
        log_error('couldn\'t parse %s as date: %s' % (repr(value), repr(e)))
    return time.time()

PRINTABLE_UNICODE_SUPER_CATEGORIES = set(['L', 'M', 'N', 'P', 'S', 'Z'])

def unicode_replace_nonprintable(value, replacement=u'_'):
    # from: https://stackoverflow.com/questions/92438/\
    #           stripping-non-printable-characters-from\
    #           -a-string-in-python
    return u''.join(map(
        lambda c:
            c if (unicodedata.category(c)[:1] in
                    PRINTABLE_UNICODE_SUPER_CATEGORIES)
                else replacement,
        value))

LOGLEVEL_DEBUG = 1
LOGLEVEL_INFO = 2
LOGLEVEL_NOTICE = 3
LOGLEVEL_ERROR = 4
LOGGING_LEVEL = LOGLEVEL_INFO

def log_error(message):
    if LOGGING_LEVEL <= LOGLEVEL_ERROR:
        log('[ERROR] ' + message)

def log_notice(message):
    if LOGGING_LEVEL <= LOGLEVEL_NOTICE:
        log('[notice] ' + message)

def log_info(message):
    if LOGGING_LEVEL <= LOGLEVEL_INFO:
        log('[info] ' + message)

def log_debug(message):
    if LOGGING_LEVEL <= LOGLEVEL_DEBUG:
        log('[debug] ' + message)

def log(message):
    # from 'maildir2gmail.py'
    # @ http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html
    sys.stderr.write('[%s]: %s\n' % (
        time.strftime('%H:%M:%S'), encode_unicode(message)))

IMAP_WORKER_OBJ = None
IMAP_WORKER_FOLDER = None
IMAP_WORKER_HOSTNAME = None
IMAP_WORKER_USERNAME = None
IMAP_WORKER_PASSWORD = None
def imap_worker_init(hostname, username, password, folder_name):
    global IMAP_WORKER_FOLDER, IMAP_WORKER_HOSTNAME
    global IMAP_WORKER_USERNAME, IMAP_WORKER_PASSWORD
    IMAP_WORKER_FOLDER = folder_name
    IMAP_WORKER_HOSTNAME = hostname
    IMAP_WORKER_USERNAME = username
    IMAP_WORKER_PASSWORD = password
    signal.signal(signal.SIGINT, imap_worker_die)
    imap_worker_setup()

def imap_worker_setup():
    global IMAP_WORKER_OBJ
    if IMAP_WORKER_OBJ is not None:
        try:
            IMAP_WORKER_OBJ.close()
        except Exception as e:
            log_error("couldnt close previous imap connection: %s" % repr(e))
    IMAP_WORKER_OBJ = IMAP4_SSL(IMAP_WORKER_HOSTNAME)
    IMAP_WORKER_OBJ.login(IMAP_WORKER_USERNAME, IMAP_WORKER_PASSWORD)
    log_info('connected \'%s\' to %s' % (
        IMAP_WORKER_USERNAME, IMAP_WORKER_HOSTNAME))
    IMAP_WORKER_OBJ.select(IMAP_WORKER_FOLDER, readonly=True)

#def imap_worker_die(signum, frame):
def imap_worker_die():
    if IMAP_WORKER_OBJ is not None:
        try:
            IMAP_WORKER_OBJ.close()
        except Exception:
            pass
    sys.exit(-1)

def imap_worker(worker_args):
    (func, args) = worker_args
    assert (IMAP_WORKER_OBJ != None)
    val = func(*args)
    return val

def imap_worker_fetch_message_refids(message_refs):
    log_info('attempting to fetch %d message ids' % len(message_refs))
    if len(message_refs) < 1:
        return []
    _typ, data = IMAP_WORKER_OBJ.fetch(
            ','.join(message_refs),
            '(BODY[HEADER.FIELDS (MESSAGE-ID)])')
    command_replies = [command_resp for _command, command_resp in data[::2]]
    message_ids = []
    for reply in command_replies:
        if reply[:11].lower() == 'message-id:':
            message_id_parts = filter(len, re.split(r'\s+', reply[11:]))
            if (len(message_id_parts) % 2) == 1:
                message_id = message_id_parts[0]
                if len(message_id) > 0:
                    log_debug('got message id %s' % repr(message_id))
                    message_ids.append(message_id)
                else:
                    log_error('message-id cannot be empty')
                    message_ids.append(None)
            else:
                log_error('unparsable message id: \%s' % message_id_parts)
                message_ids.append(None)
        else:
            log_error('invalid message-id header: %s' % repr(reply))
            message_ids.append(None)
    message_refids = zip(message_refs, message_ids)
    valid_message_refids = filter(
            lambda (_ref,mid): mid is not None, message_refids)
    return valid_message_refids

def imap_worker_download_message(
        (message_ref, message_id), local_dirname, is_dry_sync):
    try:
        _typ, data = IMAP_WORKER_OBJ.fetch(message_ref, '(RFC822)')
        message = email.message_from_string(data[0][1])
        subject = decode_header(message['subject'])
        content = message.as_string()
        safe_subject = unicode_replace_nonprintable(subject)
        log_info('downloaded \'%s\' (%d bytes)' % (
            safe_subject.encode('utf8'), len(content)))

        if not is_dry_sync:
            temp_filepath = local_message_filepath(
                    local_dirname, '', message, is_temp=True)
            with open(temp_filepath, 'wb') as msg_file:
                msg_file.write(content)

            filepath = local_message_filepath(
                    local_dirname, message_id, message)
            if os.path.exists(filepath):
                # nondeterministic, only a best effort
                raise Exception('can\'t overwrite %s' % filepath)
            os.rename(temp_filepath, filepath)

            if 'date' in message:
                mtime = parse_date_header(message['date'])
                os.utime(filepath, (time.time(), mtime))
        return True

    except Exception:
        log_error('failed to download \'%s\': %s' % (
            repr(message_id), traceback.format_exc()))
        raise

def local_message_filepath(local_dirname, mid, message, is_temp=False):
    if 'date' in message:
        timestamp = parse_date_header(message['date'])
        filename_part1 = u'%s ' % long(timestamp)
    else:
        filename_part1 = u''

    if 'subject' in message:
        filename_part2 = decode_header(message['subject'])
    else:
        filename_part2 = mid

    suffix = (u'_%s.eml' % id_generator(6 + max(0, 16 - len(filename_part2))))
    prefix = TEMP_PREFIX if is_temp else u''
    max_filename_length = min(64, 255 - (len(local_dirname) + len(prefix) + 1))
    filename = safe_message_filename(
            filename_part1 + filename_part2,
            max_filename_length, suffix)
    return os.path.join(local_dirname, prefix + filename)

def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    # from: https://stackoverflow.com/questions/2257441/\
    #           random-string-generation-with-upper-case-\
    #           letters-and-digits-in-python
    return u''.join(random.choice(chars) for _ in range(size))

def safe_message_filename(value, max_length, suffix):
    max_human_readable_id_length = max_length - len(suffix)
    safe_value = slugify(value)
    truncated_value = safe_value[:max_human_readable_id_length]
    return truncated_value + suffix

def slugify(value):
    # from Django? through:
    #   https://stackoverflow.com/questions/295135/\
    #       turn-a-string-into-a-valid-filename
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore')
    value = unicode(re.sub('[^\w\s-]', '', value).strip().lower())
    value = unicode(re.sub('[-\s]+', '-', value))
    return value

def chunks(l, n):
    for i in xrange(0, len(l), n):
        yield l[i:i+n]

def fetch_imap_message_refids(
        hostname, username, password, folder_name, limit = IMAP_FETCH_LIMIT):
    imap_obj = IMAP4_SSL(hostname)
    imap_obj.login(username, password)
    log_notice('connected \'%s\' to %s' % (username, hostname))
    imap_obj.select(folder_name, readonly=True)
    _typ, data = imap_obj.search(None, 'ALL')
    imap_obj.close()
    all_message_refs = data[0].split()
    log_notice('found %d message refs; fetching up to %d message ids' % (
        len(all_message_refs), min(len(all_message_refs), limit)))
    message_refs = all_message_refs[:limit]
    worker_pool = Pool(
            MAX_IMAP_WORKERS, imap_worker_init,
            [hostname, username, password, folder_name])
    worker_pool_args = [
            (imap_worker_fetch_message_refids, [chunk])
            for chunk in chunks(
                message_refs,
                min(1000, max(1, len(message_refs) // MAX_IMAP_WORKERS)))]
    try:
        message_refids = reduce(
                operator.concat,
                worker_pool.map(imap_worker, worker_pool_args), [])
        message_refs, message_ids = zip(*message_refids)
        message_idrefs_dict = dict(zip(message_ids, message_refs))
        valid_message_ids = filter(lambda mid: mid is not None, message_ids)
        unique_message_ids = set(valid_message_ids)
        repeated_count = len(message_refids) - len(unique_message_ids)
        unique_message_refids = [
                (message_idrefs_dict[mid], mid)
                for mid in unique_message_ids]
        del message_refids
        del message_idrefs_dict
        del valid_message_ids
        del unique_message_ids
        log_notice('successfully fetched %d message ids (%d repeated)' % (
            len(unique_message_refids), repeated_count))
        worker_pool.terminate()
        return unique_message_refids
    except Exception as e:
        log_error('rage quitting on fetch_imap_message_ids: %s' % repr(e))
        worker_pool.terminate()
        worker_pool.join()
        sys.exit(-1)

def sync(local_file_per_id, remote_refids, hostname,
        username, password, folder_name, local_dirname, is_dry_sync):
    local_ids = frozenset(local_file_per_id.keys())
    only_remote_refids = filter(
            lambda (ref,mid): mid not in local_ids,
            remote_refids)
    log_notice('trying to download %d messages' % len(only_remote_refids))
    worker_pool = Pool(
            MAX_IMAP_WORKERS, imap_worker_init,
            [hostname, username, password, folder_name])
    worker_pool_args = [
            (imap_worker_download_message,
                [message_refid, local_dirname, is_dry_sync])
            for message_refid in only_remote_refids]
    try:
        results = filter(
                lambda v: v,
                worker_pool.map(imap_worker, worker_pool_args))
        log_notice('downloaded %d messages (out of %d)' % (
            len(results), len(only_remote_refids)))
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
    cached_message_id = LOCAL_WORKER_CACHE.get(filepath)
    if cached_message_id != None:
        return (cached_message_id, filepath)

    with open(filepath, 'rb') as msg_file:
        content = msg_file.read()
        if content.endswith('\x00\x00\x00'):
            log_error('cannot parse %s: corrupted' %
                    repr(os.path.basename(filepath)))
            return
        message = email.message_from_string(content)
        message_id = message['message-id']
        del message
        del content
        if message_id is not None:
            message_id = decode_header(message_id)

        if (message_id is None) or (len(message_id) == 0):
            log_error('cannot sync %s: invalid message id (%s)' %
                    (repr(filepath), repr(message_id)))
        else:
            return (message_id, filepath)

#def imap_worker_die(signum, frame):
def local_worker_die():
    sys.exit(-1)


def fetch_local_message_ids(dirname):
    filepaths = []
    filenames = os.listdir(dirname)
    log_info('listed %s' % dirname)
    for filename in filenames:
        filepath = os.path.join(dirname, filename)
        if os.path.isfile(filepath) and filepath[:2] != TEMP_PREFIX:
            filepaths.append(filepath)

    cache_filepath = 'cached_local_message_ids.pickle'
    try:
        with open(cache_filepath, 'rb') as cache_f:
            cached_id_per_file = pickle.load(cache_f)
    except Exception:
        cached_id_per_file = {}

    log_notice('attempting to fetch message ids out of %d files' %
            len(filepaths))
    worker_pool = Pool(
            MAX_LOCAL_WORKERS, local_worker_init, [cached_id_per_file])
    try:
        file_per_id_pairs = worker_pool.map(
                parse_and_append_local_message_id, filepaths)
        defined_file_per_id_pairs = filter(
                lambda v: v is not None, file_per_id_pairs)
        new_cached_id_per_file = dict(
                [(v, k) for k, v in defined_file_per_id_pairs])
        file_per_id = dict(defined_file_per_id_pairs)

        invalid_count = len(file_per_id_pairs) - len(defined_file_per_id_pairs)
        del file_per_id_pairs
        repeated_count = len(defined_file_per_id_pairs) - len(file_per_id)
        del defined_file_per_id_pairs

        log_notice(
                'successfully fetched %d message ids'
                ' (%d were invalid, %d were repeated) out of %d files' %
                (len(file_per_id), invalid_count,
                    repeated_count, len(filepaths)))
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

def run(run_type, hostname, username, imap_folder_name, local_dirname):
    if run_type not in ['dry', 'dry_sync', 'sync']:
        raise Exception('unknown run type: %s' % run_type)
    password = getpass.getpass('Password for "%s@%s": ' % (username, hostname))
    local_file_per_id = fetch_local_message_ids(local_dirname)
    remote_refids = fetch_imap_message_refids(
            hostname, username, password, imap_folder_name)
    remote_ids = frozenset([mid for _ref,mid in remote_refids])
    if run_type == 'dry':
        local_ids = frozenset(local_file_per_id.keys())
        only_remote_refids = filter(
                lambda (ref,mid): mid not in local_ids,
                remote_refids)
        only_local = local_ids - remote_ids
        common = remote_ids & local_ids
        log_notice('found %d remote-only, %d local-only, %d common IDs' % (
            len(only_remote_refids), len(only_local), len(common)))
    elif run_type == 'dry_sync':
        sync(local_file_per_id, remote_refids,
                hostname, username, password, imap_folder_name,
                local_dirname, True)
    elif run_type == 'sync':
        sync(local_file_per_id, remote_refids,
                hostname, username, password, imap_folder_name,
                local_dirname, False)

if __name__ == '__main__':
    # Dry run:
    #   ./imap2dir.py dry imap.gmail.com \
    #       user@gmail.com '[Gmail]/All Mail' ~/email_backup/
    #
    # Dry sync:
    #   ./imap2dir.py dry_sync imap.gmail.com \
    #       user@gmail.com '[Gmail]/All Mail' ~/email_backup/
    #
    # Sync:
    #   ./imap2dir.py sync imap.gmail.com \
    #       user@gmail.com '[Gmail]/All Mail' ~/email_backup/
    #
    run(*(sys.argv[1:6]))
