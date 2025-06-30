import os
import time
import pika
import redis

# Env Var
rabbitmq_host = os.getenv('RABBITMQ_HOST', 'localhost')

# deduplication needs redis
redis_host = os.getenv('REDIS_HOST', 'localhost')
r = redis.Redis(host=redis_host, port=6379, db=0)

# RabbitMQ connection w/ retry logic
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

# create channel and declare queue
channel = connection.channel()
channel.queue_declare(queue='url_queue')

# list of test URLs
test_urls = [
   'https://example.com/article1',
    'https://example.com/article2', 
    'https://example.com/article3',
    'https://example.com/article4',
    'https://example.com/article5',
    'https://example.com/article6',
    'https://example.com/article7',
    'https://example.com/article8'
] # these are examples from the iana reserved domains

# sending each URL to the queue
for url in test_urls:
    is_new = r.sadd('crawled_urls', url)

    if is_new:
        channel.basic_publish(exchange='',
                              routing_key='url_queue',
                              body=url)
        print(f"Sent: {url}")
    else:
        print(f"Duplicate URL skipped : {url}")

print("All URLs sent to queue")

# close connection
connection.close()