# Index Fixture Shop

A small multi-language shop used to prove the Coordn8 project index.

## Installation

Install the Python package and the web front end. Nothing here is real.

## Cache eviction policy

The price cache keeps the most recently used quotes. When the cache is full the least
recently used quote is evicted first, so a burst of new products never pushes out the
quotes customers are looking at right now.

### Tuning the cache

Raise `cache_size` in config/settings.yaml to keep more quotes warm.
