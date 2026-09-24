from diskcache import Cache

from app.config import settings

# Shared file-based cache so weather/places/routing services can reuse
# the same store and stay within free-tier API quotas.
cache = Cache(settings.cache_dir)
