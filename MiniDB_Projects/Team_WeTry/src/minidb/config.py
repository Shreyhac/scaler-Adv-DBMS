"""Global configuration constants for MiniDB."""

# Size of a single page on disk and in the buffer pool, in bytes.
PAGE_SIZE = 4096

# Number of frames (pages) the buffer pool holds in memory at once.
BUFFER_POOL_SIZE = 64

# Sentinel for "no page".
INVALID_PAGE_ID = -1

# Sentinel transaction id meaning "no transaction" (used for xmin/xmax slots
# that are unset).
INVALID_TXN_ID = 0
