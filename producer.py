import xml.etree.ElementTree as ET
import requests
import os
import time
import pika
import redis

rabbitmq_host = os.getenv('RABBITMQ_HOST', 'localhost')
redis_host = os.getenv('REDIS_HOST', 'localhost')
r = redis.Redis(host=redis_host, port=6379, db=0)

max_retries = 30
for attempt in range(max_retries):
    try:
        credentials = pika.PlainCredentials('admin', 'password')
        parameters = pika.ConnectionParameters(host=rabbitmq_host, credentials=credentials)
        connection = pika.BlockingConnection(parameters)
        print(f"Connected to RMQ on attempt {attempt + 1}")
        break
    except Exception as e:
        print(f"Attempt {attempt + 1} failed: {e}")
        if attempt == max_retries - 1:
            raise
        time.sleep(2)

channel = connection.channel()
channel.queue_declare(queue='url_queue')

def discover_urls_from_sitemap(sitemap_url, max_depth=5, current_depth=0):
    try:
        if current_depth > max_depth:
            print(f"max depth {max_depth} reached for {sitemap_url}")
            return []

        print(f"fetching sitemap: {sitemap_url}")
        response = requests.get(sitemap_url, timeout=10)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        urls = []
        namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        SUB_MAX = 500

        # Check if this is a sitemap index (contains links to other sitemaps)
        if root.tag.endswith('sitemapindex'):
            print(f"Found sitemap index, fetching sub-sitemaps...")
            sub_urls = []
            sitemap_count = 0
            for sitemap_element in root.findall('.//ns:loc', namespace):
                sub_sitemap_url = sitemap_element.text
                if sub_sitemap_url and sitemap_count < 5:
                    sub_urls.extend(discover_urls_from_sitemap(sub_sitemap_url.strip(), max_depth, current_depth + 1))
                    sitemap_count += 1
                if len(sub_urls) >= SUB_MAX:
                    break
            return sub_urls[:SUB_MAX]

        for url_element in root.findall('.//ns:loc', namespace):
            url = url_element.text
            if url:
                urls.append(url.strip())
        
        if not urls:
            for url_element in root.findall('.//loc'):  # Fixed: no colon before 'loc'
                url = url_element.text
                if url:
                    urls.append(url.strip())
        
        print(f"found {len(urls)} URLs in {sitemap_url}")
        return urls[:SUB_MAX]
    except Exception as e:
        print(f"error fetching {sitemap_url}: {e}")
        return []

sitemaps = [
    "https://techcrunch.com/sitemap.xml",
    "https://www.theguardian.com/sitemaps/news.xml",
]

test_urls = [
    # 404 Not Found
    "https://httpbin.org/status/404",
    "https://example.com/definitely-does-not-exist",
    "https://www.theguardian.com/fake-article-12345",
    
    # Server Errors
    "https://httpbin.org/status/500",
    "https://httpbin.org/status/503",
    
    # Timeouts & Delays
    "https://httpbin.org/delay/10",  # 10 second delay (will timeout)
    "https://httpbin.org/delay/30",  # 30 second delay (definitely timeout)
    
    # Redirects (can cause loops)
    "https://httpbin.org/redirect/10",  # 10 redirects
    "https://httpbin.org/redirect-to?url=https://httpbin.org/redirect-to?url=https://example.com",
    
    # Malformed URLs
    "not-a-valid-url",
    "http://",
    "https://[invalid-domain]",
    
    # Rate limit triggers
    "https://api.github.com/repos/octocat/Hello-World",  # GitHub API (rate limited)
    
    # Large responses (memory bombs)
    "https://httpbin.org/stream/1000",  # Streams 1000 lines

    "https://jsonplaceholder.typicode.com/posts",  # Good JSON API
    "https://httpbin.org/status/200",  # Good
    "https://httpbin.org/status/404",  # Bad
    "https://httpbin.org/status/200",  # Good  
    "https://httpbin.org/status/500",  # Bad
]

all_urls = []

for sitemap in sitemaps:
    all_urls.extend(discover_urls_from_sitemap(sitemap, max_depth = 5))

all_urls.extend(test_urls)

print(f"Total URLs: {len(all_urls)}")

for url in all_urls:
    is_new = r.sadd('crawled_urls', url)
    MINUTE = 60 * 1000
    if is_new:
        if 'httpbin.org' in url:
            ttl_ms = 5 * MINUTE
            queue_name = 'priority_queue'
        elif 'news' in url or 'live' in url:
            ttle_ms = 30 * MINUTE
            queue_name = 'priority_queue'
        else:
            ttl_ms = None
            queue_name = 'url_queue_ttl'
        
        properties = None
        if ttl_ms:
            properties =  pika.BasicProperties(expiration=str(ttl_ms))

        channel.basic_publish(
            exchange='',
            routing_key=queue_name,
            body=url,
            properties=properties
        )
        print(f"Sent: {url} - (TTl: {ttl_ms}ms, Queue: {queue_name})")
    else:
        print(f"Duplicate URL skipped : {url}")

print("All URLs sent to queue")
connection.close()