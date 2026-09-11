"""Every threshold Causal Crew uses, in one place."""

import os

# --- Demo question ----------------------------------------------------------
# The question the demo asks: why did revenue change in CURRENT vs BASELINE?
DEMO_METRIC = "revenue"
DEMO_CURRENT_WINDOW = ("2026-08-27", "2026-09-09")
DEMO_BASELINE_WINDOW = ("2026-08-13", "2026-08-26")
DEMO_EXPECTED_LATEST_DATE = "2026-09-09"
DEMO_QUESTION = "Revenue dropped in the last two weeks. Why?"
DEMO_SUGGESTIONS = (
    "Revenue dropped in the last two weeks. Why?",
    "What explains the change in revenue over the last 3 weeks?",
    "Why did revenue move over the past month?",
    "Is last week's revenue number real?",
)

# --- Question -------------------------------------------------------------
QUESTION_DEFAULT_DAYS = 14
QUESTION_MIN_WINDOW_DAYS = 3
QUESTION_MAX_WINDOW_DAYS = 90

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS_PATH = os.path.join(_ROOT, "data", "orders.parquet")
ORDERS_BROKEN_PATH = os.path.join(_ROOT, "data", "orders_broken.parquet")
WORKSPACE_DIR = os.path.join(_ROOT, "workspaces")
CONTEXT_DIR = os.path.join(_ROOT, "context")
ENV_PATH = os.path.join(_ROOT, ".env")

DIMENSIONS = ("region", "product_category", "channel", "customer_type")
KEY_COLUMNS = ("order_id", "date", "region", "product_category",
               "channel", "customer_type", "units", "revenue")

# --- [1] Health check -------------------------------------------------------
FRESHNESS_REQUIRED = True
ROW_COUNT_TRAILING_DAYS = 28
ROW_COUNT_ROBUST_Z = 3.0          # median/MAD, MAD scaled by 1.4826
# A real business move can score several robust z on its own. A data break
# (a day loaded twice, a feed that stopped) is a large relative jump. Flag a
# day only when it is both statistically unusual and this far off the median.
ROW_COUNT_MIN_REL_DEV = 0.25
NULL_SPIKE_MAX_PP = 5.0           # percentage points vs baseline
SCALE_BREAK_RATIO = (0.2, 5.0)    # median order value, current / baseline

# --- [3] Planner -------------------------------------------------------------
PLANNER_DATA_LEADS = 2        # always include the largest single-dimension deltas
PLANNER_MAX_LEADS = 4
PLANNER_CONTEXT_DAYS = 7      # context notes from this many days before the current window
GEMINI_TIMEOUT_S = 45
# Separate from Cognee's LLM_MODEL: Gemini's free tier is 20 requests/day per
# model, and Cognee's ingest uses many. The planner makes one call per run.
PLANNER_MODEL = "gemini-2.5-flash-lite"
# Investigators get their own model so they don't share the planner's daily quota.
INVESTIGATOR_MODEL = "gemini-3.1-flash-lite-preview"

# --- [4] Investigator -------------------------------------------------------
MIN_DRILL_DEPTH = 2
CHANGE_POINT_LOOKBACK_DAYS = 56
CHANGE_POINT_MIN_SEGMENT_DAYS = 7
# Narrow into a sub-segment only if its share of the delta is at least this
# multiple of its share of baseline revenue. Below it, the effect is spread
# evenly and drilling further would just follow the biggest bucket.
DRILL_MIN_CONCENTRATION = 1.25

# --- [5] Judge --------------------------------------------------------------
MIN_ORDERS_PER_WINDOW = 200
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_CI = 0.95
BOOTSTRAP_SEED = 7
# Resample days, not orders. Resampling orders with a fixed count only sees
# order-value noise and is blind to volume changes, which is most of what
# moves revenue.
BOOTSTRAP_UNIT = "day"

# Fail seasonality if last year's same comparison moved in the same
# direction by at least this fraction of this year's effect. Effects are
# percent changes, so year-over-year growth doesn't shrink last year's.
SEASONALITY_MAX_RATIO = 0.5

# Consistency: within the finding's segment, split by each dimension not used
# to define it. Pass if sub-segments holding at least this share of baseline
# revenue move in the same direction as the aggregate.
CONSISTENCY_MIN_SHARE = 0.8

TIMING_MAX_DAYS = 3
OVERLAP_MERGE_THRESHOLD = 0.5
SUPPORTED_MIN_CONTRIBUTION = 0.30
