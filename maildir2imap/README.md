# maildir2imap
Upload maildir-style directories to IMAP; `maildir2imap` will:

1. Fetch all local message IDs from the specified directories
2. Fetch all remote message IDs (up to 1.0e9 entries) from the IMAP folder
3. ..and based on these try and upload all messages that are missing (from the IMAP folder)

It's partially based on a [2009 blog post](http://scott.yang.id.au/2009/01/migrate-emails-maildir-gmail.html) by Scott Yang and its attached script.

For performance reasons, it makes use of the following mechanisms:
* A pool of IMAP workers (size is hardcoded in `MAX_IMAP_WORKERS`)
* A pool of local workers (size is hardcoded in `MAX_LOCAL_WORKERS`); for magnetic disks they become IO-bound and it's probably not worth to go beyond 1 or 2, for SSDs they become CPU-bound and it makes sense to have as many as (CPU thread queues + 1)
* A local message ID cache that was hacked in at the last minute in order to speed up local indexing for repeated runs

Limitations:
* It ignores ID-less messages (both local and remote)
* Messages with invalid or missing dates might result in peculiar side effects
* It won't keep read/unread status
* If used with Gmail and the 'All Mail' directory, it will mark all messages in the account as read
* It's not SIGINT friendly due to the sort of use that was made of Python's [multiprocessing](https://docs.python.org/2/library/multiprocessing.html)
* It's only prepared for IMAPS (i.e. IMAP over SSL/TLS)

```shell
# Single directory (dry run):
python -OO maildir2imap.py dry imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail' ~/Maildir/cur

# Single directory (dry sync):
python -OO maildir2imap.py dry_sync imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail' ~/Maildir/cur

# Single directory (sync):
python -OO maildir2imap.py sync imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail' ~/Maildir/cur

# Multiple directories (sync):
find ~/Maildir -type d \( -name cur -or -name new \) | sed 's/^\|$/\"/g' | xargs python -OO maildir2imap.py sync imap.gmail.com user@gmail.com 'password' '[Gmail]/All Mail'
```
