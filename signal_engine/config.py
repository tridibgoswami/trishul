# =============================================================================
# Signal Engine Configuration
# Fill in your AngelOne credentials and adjust indicator settings below.
# =============================================================================

# --- AngelOne Credentials ---
API_KEY     = "YOUR_API_KEY"        # AngelOne API key
CLIENT_ID   = "YOUR_CLIENT_ID"     # e.g. A12345
MPIN        = "YOUR_MPIN"          # 4-digit MPIN
TOTP_SECRET = "YOUR_TOTP_SECRET"   # base32 secret from AngelOne TOTP setup

# --- Symbol ---
SYMBOL       = "BANKNIFTY"
SYMBOL_TOKEN = "26009"             # NSE token for BANKNIFTY index
EXCHANGE     = "NSE"
INTERVAL     = "FIVE_MINUTE"       # must match AngelOne API interval string

# --- UT Bot ---
UT_KEY_VALUE  = 5.0   # sensitivity (Pine: a)
UT_ATR_PERIOD = 9     # ATR period  (Pine: c)

# --- HMA ---
HMA_PERIOD = 10

# --- Chop Filter ---
ENABLE_CHOP_FILTER     = True
CHOP_MODE              = "Medium"   # Off | Light | Medium | Strict
USE_HMA_TREND_FILTER   = True
USE_HMA_SLOPE_FILTER   = True
USE_DISTANCE_FILTER    = True
USE_COOLDOWN_FILTER    = True
USE_RANGE_FILTER       = False
SLOPE_LEN              = 3
ATR_NORM_LEN           = 14
USE_AUTO_THRESHOLD     = True
MANUAL_SLOPE_THRESHOLD = 0.045
USE_AUTO_DISTANCE      = True
MANUAL_DISTANCE_MULT   = 0.10
COOLDOWN_BARS_MANUAL   = 3
RANGE_LOOKBACK         = 9

# --- ORB ---
ORB_DURATION_MINS = 10   # 5 | 10 | 15
USE_ORB_GATE      = True

# --- Session Management ---
ENABLE_EOD_EXIT    = True
EOD_EXIT_HOUR      = 15
EOD_EXIT_MIN       = 15
ENABLE_DAY_CONT    = True
ENABLE_GAP_FILTER  = True
GAP_FILTER_PCT     = 0.5
CONT_WINDOW_MINS   = 30
ENABLE_LATE_BLOCK  = True
LATE_BLOCK_HOUR    = 14
LATE_BLOCK_MIN     = 30

# --- Gap Day Adaptation ---
ENABLE_GAP_ADAPT       = True
LARGE_GAP_THRESH_PCT   = 0.5
ENABLE_GAP_ATR_FIX     = True
GAP_ATR_WARMUP_BARS    = 5
ENABLE_GAP_ORB_EXTEND  = True
GAP_EXTRA_ORB_MINS     = 10

# --- Engine ---
WARMUP_DAYS = 3          # previous trading days to fetch for indicator warmup
DB_PATH     = "trades.db"  # SQLite file path (relative to signal_engine/)
