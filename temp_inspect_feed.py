import feedparser
from pprint import pprint

url = 'https://www.thehindu.com/feeder/default.rss'
feed = feedparser.parse(url)
print('status', getattr(feed, 'status', None))
print('bozo', getattr(feed, 'bozo', None))
print('entries', len(feed.entries))
for i, entry in enumerate(feed.entries[:5]):
    print('--- entry', i+1, '---')
    print('title', entry.get('title'))
    print('published', entry.get('published'))
    print('updated', entry.get('updated'))
    print('published_parsed', entry.get('published_parsed'))
    print('updated_parsed', entry.get('updated_parsed'))
    print('id', entry.get('id'))
    print('link', entry.get('link'))
