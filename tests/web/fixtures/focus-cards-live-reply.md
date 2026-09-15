**Start with profiling to find where the 8 seconds go, then check database access patterns and external calls — most slow endpoints are I/O-bound, not CPU.**

**1 → Profile the endpoint.** Use `cProfile` or a WSGI middleware to measure where wall time goes: database, external API calls, template rendering, or application logic. Without this, you're guessing.

**2 → Hunt N+1 queries.** If the endpoint queries a database, check whether it's fetching related rows in a loop instead of batch-loading. This is the #1 cause of slow reports.

**3 → Check external dependencies.** If the endpoint calls other APIs, third-party services, or filesystem I/O, those often block for seconds. Look for async opportunities or caching.

**4 → Examine the payload.** If the endpoint returns a large response (many rows, deep nesting), serialization and network transfer can add seconds.

**Risk:** If you cache aggressively to hide the problem, you risk stale data and hidden bugs when the cache invalidation logic breaks.

**What does the endpoint actually do?** (Fetch data from which sources—database tables, APIs, files? Perform any calculations? How many rows in the response?)
