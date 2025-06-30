import os, time, requests
import redis
import pika
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# Env initialization
redis_host = os.getenv('REDIS_HOST', 'localhost')
rabbitmq_host = os.getenv('RABBITMQ_HOST', 'localhost')

# redis initialization
r = redis.Redis(host=redis_host, port=6379, db=0)

# rabbitmq initialization
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

# start channel connection
channel = connection.channel()
channel.queue_declare(queue='url_queue', durable=True)

# TTL of main
channel.queue_declare(
    queue='url_queue_ttl',
    durable=True,
    arguments={
        'x-message-ttl': 3600000 # 1 hour
    }
)

# dead letter queue
channel.exchange_declare(exchange='failed_exchange', exchange_type='direct')
channel.queue_declare(
    queue='failed_urls',
    durable=True,
    arguments={
        'x-message-ttl': 86400000 # 24 hours
    }
)

# Priority Queue
channel.queue_declare(
    queue='priority_queue',
    durable=True,
    arguments={
        'x-message-ttl': 1800000 # 30 min
    }
)

channel.queue_bind(exchange='failed_exchange', queue='failed_urls', routing_key='failed')

# callback function (modified for DLQ)
def process_url(ch, method, properties, body):
    url = body.decode('utf-8')
    print(f"Processing URL: {url}", flush=True)

    # Validate URL first
    try:
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        if not domain:  # Empty domain means invalid URL
            raise ValueError(f"Invalid URL: {url}")
    except Exception as e:
        print(f"INVALID URL: {url} - {e}", flush=True)
        # Send invalid URLs directly to DLQ
        channel.basic_publish(
            exchange='failed_exchange',
            routing_key='failed',
            body=url
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    # rate limiting feature
    domain = urlparse(url).netloc
    current_minute = int(time.time() // 60)
    rate_limit_key = f"ratelimit:{domain}:{current_minute}"

    current_count = r.get(rate_limit_key)
    current_count = int(current_count) if current_count else 0

    RATE_LIMIT = 10
    WINDOW_SECONDS = 30

    if current_count >= RATE_LIMIT:
        print(f"rate limit exceeded for {domain} - skipped", flush=True)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    
    r.incr(rate_limit_key)
    r.expire(rate_limit_key, WINDOW_SECONDS)

    print(f"Rate limit status for {domain}: {current_count + 1}/{RATE_LIMIT}", flush=True)

    # caching feature
    cache_key = f"content:{url}"
    cached_content = r.get(cache_key)

    if cached_content:
        print(f"cache hit - using cache for {url}", flush=True)
        content = cached_content.decode('utf-8')
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    
    # failure tracking & scraping
    failure_key = f"failures:{url}"
    failure_count = r.get(failure_key)
    failure_count = int(failure_count) if failure_count else 0

    try:
        print(f"cache miss - fetching content for {url}", flush=True)
        response = requests.get(url, timeout=2)
        print(f"Response status: {response.status_code} for {url}", flush=True)
        print(f"Response time: {response.elapsed.total_seconds():.2f}s", flush=True)
        response.raise_for_status() # raises exception for 4xx/5xx statuses

        content = f"scraped content from {url} at {time.time()}"
        print(f"SUCCESS: {url}", flush=True)
        
        if failure_count > 0:
            r.delete(failure_key)
        r.setex(cache_key, 60, content)
        print(f"success: {url}", flush=True)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except requests.exceptions.Timeout as e:
        print(f"TIMEOUT for {url}: {e}", flush=True)
        failure_count += 1
        r.setex(failure_key, 3600, failure_count)
        print(f"FAILURE #{failure_count} for {url}: {e}", flush=True)

        MAX_RETRIES = 3
        if failure_count >= MAX_RETRIES:
            print(f"Max retries exceeded - sending to DLQ {url}", flush=True)
            channel.basic_publish(exchange='failed_exchange', routing_key="failed", body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(f"retrying later (attempt {failure_count}/{MAX_RETRIES}): {url}", flush=True)
            channel.basic_publish(exchange='', routing_key='url_queue', body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)

    except requests.exceptions.HTTPError as e:
        print(f"HTTP ERROR for {url}: {e}", flush=True)
        failure_count += 1
        r.setex(failure_key, 3600, failure_count)
        print(f"FAILURE #{failure_count} for {url}: {e}", flush=True)

        MAX_RETRIES = 3
        if failure_count >= MAX_RETRIES:
            print(f"Max retries exceeded - sending to DLQ {url}", flush=True)
            channel.basic_publish(exchange='failed_exchange', routing_key="failed", body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(f"retrying later (attempt {failure_count}/{MAX_RETRIES}): {url}", flush=True)
            channel.basic_publish(exchange='', routing_key='url_queue', body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        failure_count += 1
        r.setex(failure_key, 3600, failure_count)
        print(f"FAILURE #{failure_count} for {url}: {e}", flush=True)

        MAX_RETRIES = 3
        if failure_count >= MAX_RETRIES:
            print(f"Max retires exceeded - sending to DLQ {url}", flush=True)
            # send to DLQ
            channel.basic_publish(
                exchange='failed_exchange',
                routing_key="failed",
                body=url
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(f"retrying later (attempt {failure_count}/{MAX_RETRIES}): {url}", flush=True)
            # requeue
            channel.basic_publish(exchange='', routing_key='url_queue', body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)

def process_url_auto_ack(ch, method, prperties, body):
    """Auto Acknowledgement processing"""
    url = body.decode('utf-8')
    print(f"AUTO-ACK processing {url}", flush=True)
    try:
        # Simulate work
        time.sleep(0.1)
        print(f"AUTO-ACK Success: {url}", flush=True)
    except Exception as e:
        print(f"AUTO-ACK Error (message already gone!): {url} - {e}", flush=True)

# starts the waiting loop to process the queue
print("starting to listen for messages...", flush=True)

# Manual ack from main TTL queue
channel.basic_consume(
    queue='url_queue',
    on_message_callback=process_url,
    auto_ack=False
)

# auto ack from priority queue
channel.basic_consume(
    queue='priority_queue',
    on_message_callback=process_url_auto_ack,
    auto_ack=True
    )

channel.start_consuming()