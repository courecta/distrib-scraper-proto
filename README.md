# distributed Scraper Prototype

- A distributed Scraper Prototype to learn docker, using Redis and RabbitMQ

key notes for dev:

## Setup phase

### 1. `depends_on` in the docker compose only waits for the containers to start, not for the services inside to be ready to accept conditions

### 2. 
    - 5672 = AMQP protocol port for your Python code to send/receive messages
    - 15672 = Web management UI (you'll be able to visit localhost:15672 in your browser)
    - 6379 = Redis connection port

### 3. `build .` looks for the dockerfile at that location, `.` means current directory

### 4. running `docker-compose up` makes a special network for all of our services. This private network can only be reached by service names like we've defined. Docker compose then automatically creates a mini DNS system where `service names := hostnames`.

### 5. A docker file essentially follows three steps in bringing in what's needed.

    1. Base image: the main is the package using `FROM python:3.13`
    2. Python packages: What we need for our program/service to actually run, using `redis`, `pika`, `requests`, etc. in our `requirements.txt`
    3. Copy files: we should copy those files into the docker container using `COPY requirements.txt ./` and then `RUN pip install -r requirements.txt`

### 6. A container should run the worker automatically, the producer then can be run manually by a cron job or another container
    - Producer: finds jobs, like new article URLs to add onto queue
    - Workers: running constantly, waiting for jobs to appear in queue

```python
# Pseudo-code for a worker
while True:
    message = rabbitmq.get_next_message()  # This blocks/waits until a message arrives
    if message:
        url = message.body
        scrape_website(url)
        rabbitmq.acknowledge(message)  # "I finished this job"
    # Then it immediately loops back to wait for the next message
```

### 7. The worker needs to first initalize a few things

1. First is to get environment variables with the `os.getenv()` method to get the redis and rabbitmq host. For now, we can provide simple fallbacks like getting `localhost` instead if the environment variables aren't loaded in

2. The redis client must also be initalized by the object returned by `redis.Redis(host, port, db)` where
        - host is the redis variable, `redis_host` we've defined beforehand
        - port is the port we've defined in the dockerfile `port=6379`
        - `db=0` is the default in redis for choosing the databases
3. The rabbitMQ connection must be established at this point. It follows a three part nested arugment pass
        - `channel = pika.BlockingConnection(parameters)`
        - `parameters = pika.ConnectionParameters(host=rabbitmq_host, credentials=credentials)`
        - `credentials = pika.PlainCredentials('admin', 'password')`

### 8. The next step is to actually start the main logic of the working parser. We essentially only need to start the channel connection and then we can start the infinite loop
1. The channel connection is initialized with 2 lines
        - `channel = connection.channel()` which initalizes the object `channel` with the `connection.channel()`
        - `channel.queue_declare(queue='url_queue')` declares a queue called `url_queue`. If a queue already exists, it does nothing. It only declares if a queue of that name does not exist yet
2. Then we can finally finish with these 2 lines
        - `channel.basic_consume(queue='url_queue', on_message_callback=process_url)` which tells RabbitMQ to use this function we wrote, `process_url()`
        - `channel.start_consuming()` then starts the infinite loop which somewhat resembles this pseudocode in point 6

* The process_url is defined as,

```python
def process_url(ch, method, properties, body):
    url = body.decode('utf-8')
    print(url)
    ch.basic_ack(delivery_tag=method.delivery_tag)
```

### 9. Finally, we should setup the producer module, that publishes URLs to the `url_queue`. Here, the key difference lies in that the `consume` logic is going to be replaced by the `publish` logic

```python
for url in test_urls:
    channel.basic_publish(exchange='',
                          routing_key='url_queue',
                          body=url)
    print(f"Sent: {url}")
```

Where the important key is the `routing_key` that we assign our particular queue for.
---

## Deployment Phase

### 10. To deploy, docker must recognize that the localhost, or whatever shared host must be where the port to the redis and rabbitMQ containers connect to.

-  You must either set the environment variable manually by using `RABBITMQ_HOST=localhost`
- or use some sort of virtual environment like `venv`
- Or, alternatively for a much more holistic approach, we can establish a temporary docker container by using this command

`docker run --rm -it --network distrib-scraper-proto_default -e RABBITMQ_HOST=rabbitmq -v $(pwd):/app -w /app python:3.13 bash`

here,
    - `docker run` starts a new container
    - `--rm` flag removes the container when it exits (temporary)
    - `it` interactive TTY (it gives you a shell you can type in)
    - `--network distrib-scraper-proto_default` connects to the same network as your other container so that i can reach RabbitMQ at `rabbitmq:5672`
    - `-e RABBITMQ_HOST=rabbitmq` gives it this environment variable `rabbitmq` so that it can access this inside the container
    - `-v $(pwd):/app` mounts the current directory inside the container at `/app` so that the container can see our `producer.py`
    - `w /app` sets the working directory to `/app` inside the container for convenience sake
    - `python:3.13` uses the same python image as our worker
    - `bash` runs the bash shell when the container starts

### 11. Once you enter the shell, you can run `pip install pika && python producer.py` to execute. At the same time, remember in another terminal to actually first run the docker setup by using `docker-compose up --build -d`.

- Here, the `--build` flag forces a rebuild of the docker images before starting the containers. If not used, current updates to teh files may rely on previously built, stale images, which don't reflect the current images
- `-d` flag is short for `--detach` which runs the container in detached mode, which returns control of the terminal back to you, and the containers become a background process (makes sense as docker is a daemon process in the first place), if not, it simply keeps running which is also fine, since you can just run another terminal

### 12. Then, we can run `docker-compose logs -f scraper` to see if anything comes through. If it does you should see that from the worker's side, it should look something like this

```
$ docker-compose logs scraper
scraper-1  | Attempt 1 failed: 
scraper-1  | Attempt 2 failed: 
scraper-1  | Attempt 3 failed: 
scraper-1  | Attempt 4 failed: 
scraper-1  | Attempt 5 failed: 
scraper-1  | Attempt 6 failed: 
scraper-1  | Attempt 7 failed: 
scraper-1  | Attempt 8 failed: 
scraper-1  | Attempt 9 failed: 
scraper-1  | Connected to RMQ on attempt 10
scraper-1  | starting to listen for messages...
scraper-1  | Processing URL: https://example.com/article1
scraper-1  | Finished processing: https://example.com/article1
scraper-1  | Processing URL: https://example.com/article2
scraper-1  | Finished processing: https://example.com/article2
scraper-1  | Processing URL: https://example.com/article3
scraper-1  | Finished processing: https://example.com/article3
...
```

- note that here, we use the `-f` or `--follow` flag to tell the logger to stream the logs in real time. If we don't use it, it will just print the existing logs up till that moment then exits immediately (this will need timing if your producer to worker process is really fast or short)

### 13. We can further use horizontal scaling to our worker setup here by taking advantage of docker's capabilities, which is distributed deployment by adding on the `--scale` flag as such

`docker-compose up --scale scraper=4 -d`

- Here, the scale flag creates multiple instances of the same service we tell it, here scraper for instance and tells it to make 4 of them, all running on a single VM instance

You should see something like this if we test once again,

```
scraper-2  | Attempt 14 failed: 
scraper-2  | Attempt 15 failed: 
scraper-2  | Connected to RMQ on attempt 16
scraper-2  | starting to listen for messages...
Ascraper-4  | Processing URL: https://example.com/article1
scraper-4  | Finished processing: https://example.com/article1
scraper-2  | Processing URL: https://example.com/article2
scraper-2  | Finished processing: https://example.com/article2
scraper-2  | Processing URL: https://example.com/article6
scraper-2  | Finished processing: https://example.com/article6
scraper-3  | Processing URL: https://example.com/article3
scraper-3  | Finished processing: https://example.com/article3
scraper-3  | Processing URL: https://example.com/article7
scraper-3  | Finished processing: https://example.com/article7
scraper-1  | Processing URL: https://example.com/article4
scraper-1  | Finished processing: https://example.com/article4
scraper-1  | Processing URL: https://example.com/article8
scraper-1  | Finished processing: https://example.com/article8
scraper-4  | Processing URL: https://example.com/article5
scraper-4  | Finished processing: https://example.com/article5
scraper-1  | Processing URL: https://example.com/article4
scraper-1  | Finished processing: https://example.com/article4
scraper-3  | Processing URL: https://example.com/article3
scraper-3  | Finished processing: https://example.com/article3
scraper-3  | Processing URL: https://example.com/article7
scraper-3  | Finished processing: https://example.com/article7
scraper-4  | Processing URL: https://example.com/article1
scraper-4  | Finished processing: https://example.com/article1
scraper-4  | Processing URL: https://example.com/article5
scraper-4  | Finished processing: https://example.com/article5
scraper-1  | Processing URL: https://example.com/article8
scraper-2  | Processing URL: https://example.com/article2
scraper-2  | Finished processing: https://example.com/article2
scraper-2  | Processing URL: https://example.com/article6
scraper-2  | Finished processing: https://example.com/article6
scraper-1  | Finished processing: https://example.com/article8
...
```

---

## Redis Features

### 14. URL deduplication

URL deduplication is a key feature Redis excels at to avoid scraping the same URL twice.

```python
# Cache successful responses
cache_key = f"content:{url}"
r.setex(cache_key, 60, content)  # Cache for 60 seconds

# Check cache before scraping
cached_content = r.get(cache_key)
```

* we use `is_new = r.sadd('crawled_urls', url)` in the producer to execute deduplication first
* `setex` here caches for our failure counter later

### 15. Rate limiting using counters with expirations

Rate limiting uses redis counters to temporarily cache urls we set at keys.

* `r.incr(rate_limit_key)` increments a counter for that key, or creates one if it doesn't exist
* `r.expire(rate_limit_key, 60)` sets an expiration time for that key

### 16. URL Tracking with lists/sets

Here, we look at this,

```python
failure_key = f"failures:{url}"
failure_count = r.get(failure_key)
failure_count = int(failure_count) if failure_count else 0
```

which goes hand in hand with the next feature from rabbitmq, where we can track each url as an increment of how many times it has failed.

---

## RabbitMQ Features

### 17. Dead Letter Queues (DLQ) 

```python
channel.exchange_declare(exchange='failed_exchange',
exchange_type='direct')
channel.queue_declare(
    queue='failed_urls',
    durable=True,
    arguments={
        'x-message-ttl': 86400000 # 24 hours
    }
)

channel.queue_bind(exchange='failed_exchange', queue='failed_urls', routing_key='failed')
```

We can create what is called a dead letter queue in 2 parts. First we have an exchange, which acts as a middleman message router. Here, we also create a queue called `failed_urls` to algon with `failed_exchange`. So any message that goes through the `failed_exchange` with the routing key, `failed`, is filtered into the `failed_urls` queue. Once it has been filtered into the DLQ, it stays there until it expires.

We also have what we call a queue bind here to actually allow the flow from the exchange to the queue.

```python
MAX_RETRIES = 3
        if failure_count >= MAX_RETRIES:
            print(f"Max retries exceeded - sending to DLQ {url}", flush=True)
            channel.basic_publish(exchange='failed_exchange', routing_key="failed", body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(f"retrying later (attempt {failure_count}/{MAX_RETRIES}): {url}", flush=True)
            channel.basic_publish(exchange='', routing_key='url_queue', body=url)
            ch.basic_ack(delivery_tag=method.delivery_tag)
```

Our producer must also handle this to actually implement this queue, as you can see with either too many fails making it publihs into the exchange with the key, or a requeue system if aiming to retry again.

### 18. Message TTL & Expiration

```python
channel.queue_declare(
    queue='url_queue_ttl',
    durable=True,
    arguments={
        'x-message-ttl': 3600000 # 1 hour
    }
)
channel.queue_declare(
    queue='failed_urls',
    durable=True,
    arguments={
        'x-message-ttl': 86400000 # 24 hours
    }
)
channel.queue_declare(
    queue='priority_queue',
    durable=True,
    arguments={
        'x-message-ttl': 1800000 # 30 min
    }
)
```

Here, we can establish these 3 queues, modifying 2 we already have, and make a priority queue for urgent or fresh content. We also make them durable such that they live beyond restarts, but we limit how long they live. Theoretically, 2 types of TTL exists,

1. Queue-level TTL: All messages in queue expire after X time
2. Message-level TTL: Each message has individual expiration

* Combined: Min(queue_ttl, message_ttl) wins

We use this to keep fresh content and clean stale jobs while managing resources to prevent infinite queue growth.

### 19. Auto Acknowledgement

Acknowledgement as covered basically, essentially is a throwback message after processing by the worker to RabbitmQ to delete the message in queue. But, if you are either using it for:

1. High volume, low value messages who's tradeoff does warrant a good tradeoff of losing the 0.1% of data for 10x faster processing.

2. Idempotent Operations like cache warming, cleanups, or emails where re-running is harmless, so a message lost is acceptable.

3. RTS systems like UDP cases where stale data is worse than missing data

4. Fire and forget logging where perfect accuracy isn't worth the performance cost

5. Memory-constrained or edge computing systems

```python
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
```

Here, we implement

```python
def process_url_auto_ack(ch, method, prperties, body):
    """Auto Acknowledgement processing"""
    url = body.decode('utf-8')
    print(f"AUTO-ACK processing {url}", flush=True)

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
```

and open up 2 channel consumes for both types of queues now.

### 20. Extra features

* Invalid URL detection before processing
* Different exception type handling to differentiate between timeouts vs HTTPError or other general errors

---

## Data Flow Graph

```
                    DISTRIBUTED SCRAPER DATA FLOW
                    =============================

┌─────────────────┐    1. Discover URLs      ┌──────────────────┐
│  Real Sitemaps  │◄─────────────────────────┤ sitemap_producer │
│   TechCrunch    │                          │     .py          │
│   Guardian      │                          └────────┬─────────┘
└─────────────────┘                                   │
                                                      │ 2. Deduplication
┌─────────────────┐    URLs                           │    Check
│   Test URLs     │◄──────────────────────────────────┤
│  httpbin.org    │                          ┌────────▼────────┐
│  Malformed      │                          │     REDIS       │
│  Timeouts       │                          │ crawled_urls    │
└─────────────────┘                          │ (SET for dedup) │
                                             └────────┬────────┘
                                                      │
                                           3. Route by URL type
                                                      │
                          ┌───────────────────────────┼───────────────────────────┐
                          │                           │                           │
                          ▼                           ▼                           ▼
                ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
                │ priority_queue  │         │  url_queue_ttl  │         │  failed_urls    │
                │                 │         │                 │         │     (DLQ)       │
                │ TTL: 30min      │         │ TTL: 1 hour     │         │ TTL: 24 hours   │
                │ • httpbin URLs  │         │ • Regular URLs  │         │ • Failed URLs   │
                │ • news/live     │         │ • Bulk content  │         │ • Invalid URLs  │
                └─────────┬───────┘         └─────────┬───────┘         └─────────────────┘
                          │                           │                           ▲
                          │ 4. Consumer pulls         │                           │
                          │    messages               │                           │
                          ▼                           ▼                           │
                ┌─────────────────┐         ┌─────────────────┐                   │
                │    WORKER       │         │    WORKER       │                   │
                │ (auto_ack=True) │         │(auto_ack=False) │                   │
                │                 │         │                 │                   │
                │ Fast processing │         │ Reliable proc.  │                   │
                └─────────┬───────┘         └─────────┬───────┘                   │
                          │                           │                           │
                          │ 5. Process URL            │                           │
                          │                           │                           │
                          ▼                           ▼                           │
                ┌─────────────────┐         ┌─────────────────┐                   │
                │ Rate Limiting   │         │ Rate Limiting   │                   │ 7. Max retries
                │                 │         │                 │                   │    exceeded
                │  REDIS:         │         │  REDIS:         │                   │
                │ ratelimit:      │         │ ratelimit:      │                   │
                │ domain:minute   │         │ domain:minute   │                   │
                └─────────┬───────┘         └─────────┬───────┘                   │
                          │                           │                           │
                          │ 6. HTTP Request           │                           │
                          │                           │                           │
                          ▼                           ▼                           │
                ┌─────────────────────────────────────────────────────────────────┤
                │                    RESPONSE CACHE                               │
                │                                                                 │
                │  REDIS: content:{url} → cached response (60s TTL)               │
                │                                                                 │
                │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
                │  |   SUCCESS   │  │   TIMEOUT   │  │   404/500   │              │
                │  │             │  │             │  │             │              │
                │  │ Cache resp. │  │ Track fail  │  │ Track fail  │              │
                │  │ Acknowledge │  │ Increment   │  │ Increment   │              │
                │  └─────────────┘  └─────────────┘  └─────────────┘              │
                └─────────────────────────────────────────────────────────────────┘
                          │                           │                           │
                          │                           │ 6. Failure Tracking       │
                          │                           │                           │
                          ▼                           ▼                           │
                ┌─────────────────┐         ┌─────────────────┐                   │
                │   AUTO-ACK      │         │  MANUAL-ACK     │                   │
                │                 │         │                 │                   │
                │ Message gone    │         │ REDIS:          │                   │
                │ immediately     │         │ failures:{url}  │───────────────────┘
                │                 │         │ (retry counter) │ 
                └─────────────────┘         └─────────────────┘
                                                      │
                                                      │ 8. Requeue or DLQ
                                                      │
                                            ┌─────────▼───────┐
                                            │ Retry Decision  │
                                            │                 │
                                            │ < 3 fails:      │
                                            │ → Requeue       │
                                            │                 │
                                            │ ≥ 3 fails:      │
                                            │ → Send to DLQ   │
                                            └─────────────────┘

                              MONITORING & DEBUGGING
                              =====================
                         
                    ┌─────────────────┐     ┌─────────────────┐
                    │  RabbitMQ UI    │     │   Redis CLI     │
                    │ localhost:15672 │     │   Query data    │
                    │                 │     │                 │
                    │ • Queue depths  │     │ • Cache status  │
                    │ • Message rates │     │ • Rate limits   │
                    │ • DLQ contents  │     │ • Failure count │
                    │ • TTL timers    │     │ • Dedup sets    │
                    └─────────────────┘     └─────────────────┘
```

---

## Important commands

* `$ docker run --rm -it --network distrib-scraper-proto_default -e RABBITMQ_HOST=rabbitmq -e REDIS_HOST=redis -v $(pwd):/app -w /app python:3.13 bash`
* `$ docker exec -it distrib-scraper-proto-redis-1 redis-cli`
* `$ docker exec -it distrib-scraper-proto-rabbitmq-1 rabbitmqctl list_queues` 
* Most docker commands for building and logging