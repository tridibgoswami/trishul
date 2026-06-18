# =============================================================================
# Classic Signal Engine Configuration
# Python port of SVMKR_UT_HMA_ORB_ChopNoADX.pine (continuous always-in-market
# flip system — no session management, no EOD exit, no ORB gating).
# Fill in your AngelOne credentials and adjust indicator settings below.
# =============================================================================

# --- AngelOne Credentials ---
API_KEY     = "YOUR_API_KEY"        # AngelOne API key
CLIENT_ID   = "YOUR_CLIENT_ID"      # e.g. A12345
MPIN        = "YOUR_MPIN"           # 4-digit MPIN
TOTP_SECRET = "YOUR_TOTP_SECRET"    # base32 secret from AngelOne TOTP setup

# --- Symbol ---
SYMBOL       = "BANKNIFTY"
SYMBOL_TOKEN = "26009"              # NSE token for BANKNIFTY index
EXCHANGE     = "NSE"
INTERVAL     = "FIVE_MINUTE"        # must match AngelOne API interval string

# --- UT Bot (Pine: a, c) ---
UT_KEY_VALUE  = 5.0
UT_ATR_PERIOD = 9

# --- HMA (Pine: n) ---
HMA_PERIOD = 10

# --- Chop Filter - No ADX ---
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

# --- Engine ---
WARMUP_DAYS = 3            # previous trading days to fetch for indicator warmup
DB_PATH     = "trades.db"  # SQLite file path (relative to classic_signal_engine/)
