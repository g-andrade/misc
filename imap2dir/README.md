# imap2dir
Download messages contained in an IMAP folder to a local directory; `imap2dir` will:

1. Parse all local message IDs from files contained in the specified directory
2. Fetch all remote message IDs (up to 1.0e15 entries) from the IMAP folder
3. ..and based on these try and download all (but only) the messages that are missing (from the local folder)

The script will also try to set local modification times to the corresponding 'date' header values. The generated filenames are based on timestamp, subject (filtered for safety) and a random suffix.

This is partially based on a [2009 blog post](http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html) by Scott Yang and its attached script.

For performance reasons, it makes use of the following mechanisms:
* A pool of IMAP workers (size is hardcoded in `MAX_IMAP_WORKERS`)
* A pool of local workers (size is hardcoded in `MAX_LOCAL_WORKERS`); for magnetic disks they become IO-bound and it's probably not worth to go beyond 1 or 2, for SSDs they become CPU-bound and it makes sense to have as many as (CPU thread queues + 1)
* A local message ID cache that was hacked in at the last minute in order to speed up local indexing for repeated runs

Limitations:
* It ignores ID-less messages (both local and remote)
* Information like read/unread status, labels, etc. will be lost
* It's not SIGINT friendly due to the sort of use that was made of Python's [multiprocessing](https://docs.python.org/2/library/multiprocessing.html)
* It's only prepared for IMAPS (i.e. IMAP over SSL/TLS)

```shell
# Dry run:
./imap2dir.py dry imap.gmail.com user@gmail.com '[Gmail]/All Mail' ~/gmail_backup/

# Dry sync:
./imap2dir.py dry_sync imap.gmail.com user@gmail.com '[Gmail]/All Mail' ~/gmail_backup/

# Sync:
./imap2dir.py sync imap.gmail.com user@gmail.com '[Gmail]/All Mail' ~/gmail_backup/
```
