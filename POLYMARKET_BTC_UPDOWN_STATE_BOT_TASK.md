# TASK: Реалізувати Polymarket BTC UP/DOWN State-Pricing Bot у PAPER режимі

## 1. Контекст

Потрібно реалізувати окремого бота для short-term BTC UP/DOWN ринків Polymarket.

Це не generic market-making bot, не DCA, не martingale і не простий next-candle predictor. Це **state-pricing bot**, який оцінює поточний стан round-а, порівнює власну fair probability з реальною ціною контракту на Polymarket і відкриває тільки PAPER угоду, якщо є статистичний value.

Базова дослідницька логіка вже перевірена на BTCUSDT:

- source candles: Binance BTCUSDT 5m;
- round interval: 15m;
- sample: приблизно 17k round-ів;
- загальний UP/DOWN baseline близький до 50/50;
- after_5m модель показала приблизно 70% accuracy;
- after_10m модель показала приблизно 83% accuracy;
- але високий prediction probability не означає автоматично прибуткову угоду, бо Polymarket ask може вже враховувати цей edge.

Ключова торгова умова:

```text
TRADE only if:
  real_polymarket_ask <= historical_probability - safety_buffer
```

Тобто бот має бути не просто предиктором напрямку, а **value-entry decision engine**.

---

## 2. Приклад специфічного Polymarket URL

Polymarket BTC UP/DOWN ринки мають специфічні URL/slug, наприклад:

```text
https://polymarket.com/uk/event/btc-updown-5m-1780652400
```

Потрібно підтримати такі форми input:

```text
https://polymarket.com/uk/event/btc-updown-5m-1780652400
https://polymarket.com/event/btc-updown-5m-1780652400
btc-updown-5m-1780652400
btc-updown-15m-1780652400
```

У slug є timestamp:

```text
btc-updown-5m-1780652400
```

Не можна сліпо вважати, що timestamp завжди означає round start або round close. Код має перевірити timestamp проти Polymarket event/market metadata.

---

## 3. Ціль першої версії

Реалізувати **PAPER-only bot**, який:

1. Виявляє поточний BTC UP/DOWN 5m або 15m market на Polymarket.
2. Підтримує explicit event URL / slug.
3. Парсить timeframe і timestamp зі slug.
4. Через Polymarket API знаходить event/market metadata.
5. Отримує condition id / market id / clob token ids для UP і DOWN.
6. Отримує orderbook, best bid, best ask, spread, liquidity.
7. Отримує Binance BTCUSDT 5m OHLCV/current price.
8. Визначає стан round-а: round_open, current BTC price, current_side, distance bucket, volatility bucket, candle pattern, pattern combo, stage.
9. Знаходить historical probability через state-bucket rules.
10. Рахує fair_price, max_buy_price, edge_vs_ask.
11. Приймає рішення TRADE або SKIP.
12. У PAPER режимі симулює купівлю по best ask.
13. Зберігає повний decision snapshot для кожної TRADE/SKIP дії.
14. Mark-to-market відстежує відкриту PAPER позицію.
15. Після market resolution робить settlement.
16. Генерує звіти по кожній угоді і загальну paper performance статистику.

---

## 4. Заборонено у v1

- Live trading.
- Реальні ордери.
- DCA.
- Averaging down.
- Martingale.
- Partial fills.
- Торгівля без перевірки spread/liquidity.
- Торгівля, якщо ask > max_buy_price.
- Торгівля, якщо Polymarket metadata/orderbook/Binance price stale.
- Scraping UI, якщо доступний API.
- Припущення про timestamp без перевірки metadata.

---

## 5. Архітектура

Реалізувати модульно.

```text
src/
  polymarket_round_bot/
    __init__.py
    config.py
    models.py
    binance_client.py
    polymarket_discovery.py
    polymarket_clob_client.py
    candle_features.py
    round_state.py
    probability_rules.py
    signal_engine.py
    paper_broker.py
    risk_manager.py
    settlement.py
    storage.py
    reporting.py
    runner.py

scripts/
  run_polymarket_round_paper.py
  paper_report.py
  export_paper_trades.py
  inspect_paper_trade.py

config/
  btc_updown_state_rules.json
  example.env

tests/
  test_url_parser.py
  test_polymarket_discovery.py
  test_round_state.py
  test_probability_rules.py
  test_signal_engine.py
  test_risk_manager.py
  test_paper_broker.py
  test_settlement.py
  test_reporting.py
```

Якщо в існуючому проекті вже є інша структура, інтегрувати additive-only, не ламаючи існуючий код.

---

## 6. Config

Додати `.env` / config parameters:

```env
BOT_MODE=paper
BTC_SYMBOL=BTCUSDT
DEFAULT_TIMEFRAME=5m
POLYMARKET_EVENT_URL=
POLYMARKET_EVENT_SLUG=
SAFETY_BUFFER=0.04
MAX_SPREAD=0.03
MIN_LIQUIDITY_USD=25
MAX_POSITION_USD=5
MAX_DAILY_LOSS_USD=10
MAX_OPEN_POSITIONS=1
ALLOW_AFTER_5M=true
ALLOW_AFTER_10M=true
MIN_HISTORICAL_PROBABILITY=0.60
MIN_EDGE=0.04
MIN_SAMPLES=60
PAPER_STARTING_BALANCE_USD=100
BINANCE_PRICE_MAX_AGE_SECONDS=10
POLY_ORDERBOOK_MAX_AGE_SECONDS=5
POLY_MARKET_METADATA_MAX_AGE_SECONDS=60
PAPER_MARK_INTERVAL_SECONDS_5M=10
PAPER_MARK_INTERVAL_SECONDS_15M=15
```

---

## 7. URL parser

Реалізувати parser, який приймає URL або slug.

### Input examples

```text
https://polymarket.com/uk/event/btc-updown-5m-1780652400
https://polymarket.com/event/btc-updown-5m-1780652400
btc-updown-5m-1780652400
btc-updown-15m-1780652400
```

### Output

```json
{
  "asset": "BTC",
  "market_type": "UPDOWN",
  "timeframe": "5m",
  "timestamp": 1780652400,
  "slug": "btc-updown-5m-1780652400"
}
```

### Tests

- parses `/uk/event/btc-updown-5m-1780652400`;
- parses `/event/btc-updown-5m-1780652400`;
- parses slug only;
- rejects invalid slug;
- rejects unsupported asset;
- rejects unsupported timeframe;
- extracts timestamp as integer;
- preserves original slug.

---

## 8. Polymarket discovery layer

Реалізувати `polymarket_discovery.py`.

### Обов’язки

1. Прийняти explicit URL або slug.
2. Знайти event/market через Polymarket API.
3. Отримати event id, event slug, market id, condition id, clob token ids, outcome names, UP token id, DOWN token id, start time, end time, active/closed status, accepting orders status, resolved outcome.
4. Валідувати, що це саме BTC UP/DOWN market.
5. Валідувати, що timeframe зі slug збігається з metadata.
6. Валідувати timestamp проти start/end metadata.
7. Якщо explicit URL не переданий — знайти поточний active BTC UP/DOWN market автоматично.

### Timestamp alignment log

```json
{
  "slug_timestamp": 1780652400,
  "market_start_ts": "...",
  "market_end_ts": "...",
  "timestamp_alignment": "MATCHES_START | MATCHES_END | OFFSET | UNKNOWN"
}
```

---

## 9. Polymarket CLOB/orderbook client

Реалізувати `polymarket_clob_client.py`.

Потрібно отримувати для UP і DOWN token IDs:

```json
{
  "token_id": "...",
  "best_bid": 0.68,
  "best_ask": 0.70,
  "spread": 0.02,
  "ask_size": 123.4,
  "bid_size": 98.1,
  "top_5_bids": [],
  "top_5_asks": [],
  "liquidity_usd_estimate": 250.0,
  "received_at_utc": "..."
}
```

Orderbook snapshot не можна використовувати, якщо:

```text
now - received_at_utc > POLY_ORDERBOOK_MAX_AGE_SECONDS
```

Якщо stale:

```text
SKIP reason = stale_orderbook
```

---

## 10. Binance client

Реалізувати `binance_client.py`.

Primary endpoint:

```text
https://data-api.binance.vision/api/v3/klines
```

Fallback:

```text
https://api.binance.com/api/v3/klines
```

Requirements:

- retry/backoff;
- timeout;
- user-agent;
- no crash on transient network errors;
- return recent 5m candles;
- return current/latest BTC price;
- timestamp freshness check.

Якщо stale:

```text
SKIP reason = stale_binance_data
```

---

## 11. Candle feature logic

Формули:

```text
body = close - open
body_abs = abs(close - open)
range = high - low
upper_wick = high - max(open, close)
lower_wick = min(open, close) - low
body_to_range = body_abs / range
upper_wick_to_range = upper_wick / range
lower_wick_to_range = lower_wick / range
close_position_in_range = (close - low) / range
```

Flags:

```text
doji: body_to_range <= 0.10
small_body: body_to_range <= 0.25
strong_body: body_to_range >= 0.65
long_upper_wick: upper_wick_to_range >= 0.45
long_lower_wick: lower_wick_to_range >= 0.45
```

Patterns:

```text
strong_bull_close_near_high
strong_bear_close_near_low
normal_bull
normal_bear
bull_long_upper_wick
bull_long_lower_wick
bear_long_upper_wick
bear_long_lower_wick
doji_long_upper_wick
doji_long_lower_wick
doji_two_long_wicks
weak_bull
weak_bear
flat
```

---

## 12. Round state logic

### Для 15m market

Round складається з 3 x 5m candles:

```text
c0 = first 5m candle
c1 = second 5m candle
c2 = third 5m candle
round_open = c0.open
round_close = c2.close
```

Stages:

```text
AFTER_5M: uses c0
AFTER_10M: uses c0 + c1
```

Fallback target:

```text
UP якщо round_close > round_open
DOWN якщо round_close < round_open
```

### Для 5m market

5m market коротший, тому немає повноцінного `AFTER_5M` всередині самого 5m round-а.

Для 5m реалізувати `CUSTOM_5M_STATE`:

- round_open з metadata/Binance;
- current BTC price;
- current_side;
- distance_bucket;
- local micro-candle/tick state, якщо доступно;
- якщо немає достатніх внутрішніх candles — використовувати current price distance + latest 5m developing candle cautiously.

---

## 13. Current side

```text
ABOVE_OPEN якщо current_price > round_open
BELOW_OPEN якщо current_price < round_open
AT_OPEN якщо abs(current_price / round_open - 1) < tiny_threshold
```

---

## 14. Distance buckets

```text
D_0_005pct      = 0.00% – 0.05%
D_005_010pct    = 0.05% – 0.10%
D_010_020pct    = 0.10% – 0.20%
D_020_035pct    = 0.20% – 0.35%
D_035_050pct    = 0.35% – 0.50%
D_GT_050pct     = >0.50%
```

---

## 15. Volatility buckets

Volatility bucket має рахуватись тільки на попередніх завершених round-ах, без leakage.

Базовий показник:

```text
prev_16_abs_return_mean
```

Buckets:

```text
VOL_LOW
VOL_NORMAL
VOL_HIGH
```

Розділення через 33% і 66% quantiles на historical sample або rolling calibration window.

---

## 16. Probability rules

На першій версії можна використати JSON rules, згенеровані research script-ом.

Файл:

```text
config/btc_updown_state_rules.json
```

Формат:

```json
[
  {
    "rule_id": "btc_15m_after5_above_d005010_low_normalbull",
    "stage": "AFTER_5M",
    "current_side": "ABOVE_OPEN",
    "distance_bucket": "D_005_010pct",
    "volatility_bucket": "VOL_LOW",
    "pattern": "normal_bull",
    "recommended_side": "UP",
    "historical_probability": 0.764259,
    "samples": 263,
    "median_round_return": 0.000643,
    "return_aligned": true
  }
]
```

Rule lookup:

1. Exact: stage + current_side + distance_bucket + volatility_bucket + pattern.
2. Fallback: stage + current_side + distance_bucket + pattern.
3. Fallback: stage + current_side + distance_bucket.
4. Else no-trade.

No-trade if:

```text
samples < MIN_SAMPLES
historical_probability < MIN_HISTORICAL_PROBABILITY
return_aligned != true
```

---

## 17. Signal decision

TRADE decision object:

```json
{
  "decision": "TRADE",
  "side": "UP",
  "market_slug": "btc-updown-5m-1780652400",
  "event_url": "https://polymarket.com/uk/event/btc-updown-5m-1780652400",
  "token_id": "...",
  "stage": "AFTER_5M",
  "current_side": "ABOVE_OPEN",
  "distance_bucket": "D_005_010pct",
  "volatility_bucket": "VOL_LOW",
  "pattern": "normal_bull",
  "rule_id": "btc_15m_after5_above_d005010_low_normalbull",
  "rule_match_type": "exact",
  "samples": 263,
  "historical_probability": 0.764259,
  "fair_price": 0.764259,
  "safety_buffer": 0.04,
  "max_buy_price": 0.724259,
  "market_ask": 0.69,
  "edge_vs_ask": 0.074259,
  "spread": 0.02,
  "size_usd": 5,
  "reason": "ask <= max_buy_price"
}
```

SKIP decision object:

```json
{
  "decision": "SKIP",
  "reason": "ask_above_max_buy_price",
  "fair_price": 0.764259,
  "max_buy_price": 0.724259,
  "market_ask": 0.78,
  "edge_vs_ask": -0.015741
}
```

---

## 18. Entry conditions

Бот може створити PAPER trade тільки якщо всі умови виконані:

```text
market_active == true
market_closed == false
market_accepting_orders == true
selected_best_ask is not None
0 < selected_best_ask < 1
selected_best_ask <= max_buy_price
edge_vs_ask >= MIN_EDGE
selected_spread <= MAX_SPREAD
selected_ask_size >= requested_size_usd
liquidity_usd_estimate >= MIN_LIQUIDITY_USD
samples >= MIN_SAMPLES
historical_probability >= MIN_HISTORICAL_PROBABILITY
risk_allowed == true
Binance data not stale
Polymarket orderbook not stale
market metadata not stale
no existing position on same market
open_positions_count < MAX_OPEN_POSITIONS
daily_realized_pnl > -MAX_DAILY_LOSS_USD
```

---

## 19. Risk manager

Paper mode risk rules:

```text
MAX_POSITION_USD
MAX_OPEN_POSITIONS
MAX_DAILY_LOSS_USD
MIN_LIQUIDITY_USD
MAX_SPREAD
```

No averaging/DCA in v1.

One position per market.

If any risk rule fails:

```text
SKIP reason = risk_rejected:{reason}
```

---

## 20. Audit-grade PAPER execution

Paper trading має бути максимально наближений до live execution, щоб кожну угоду можна було проаналізувати після завершення.

Для PAPER BUY використовувати **best ask**, не mid і не last.

```text
entry_price = selected_best_ask
shares = size_usd / entry_price
cost_usd = shares * entry_price
```

No partial fills in v1.

Якщо available ask size < requested size:

```text
SKIP reason = insufficient_ask_size
```

---

## 21. Decision snapshot

Для кожного TRADE або SKIP записувати повний snapshot.

Core fields:

```text
decision_id
timestamp_utc
market_slug
event_url
timeframe
round_start_ts
round_end_ts
seconds_to_expiry
stage
side_checked
selected_side
outcome_token_id
opposite_token_id
decision
skip_reason
```

BTC state:

```text
round_open_price
current_btc_price
current_side
distance_from_round_open
distance_bucket
volatility_bucket
candle_pattern
pattern_combo
c0_open
c0_high
c0_low
c0_close
c0_volume
c1_open
c1_high
c1_low
c1_close
c1_volume
source_exchange
source_symbol
binance_data_received_at_utc
binance_data_age_seconds
```

Polymarket snapshot:

```text
up_best_bid
up_best_ask
down_best_bid
down_best_ask
up_spread
down_spread
selected_best_bid
selected_best_ask
selected_spread
selected_ask_size
selected_bid_size
orderbook_depth_top_5_json
liquidity_usd_estimate
market_active
market_closed
market_accepting_orders
orderbook_received_at_utc
orderbook_age_seconds
metadata_received_at_utc
metadata_age_seconds
```

Signal snapshot:

```text
rule_id
rule_match_type
samples
historical_probability
fair_price
safety_buffer
max_buy_price
market_ask
edge_vs_ask
min_edge_required
recommended_side
return_aligned
```

Risk snapshot:

```text
requested_size_usd
max_position_usd
open_positions_count
max_open_positions
daily_realized_pnl
max_daily_loss_usd
risk_allowed
risk_reject_reason
```

---

## 22. Paper position record

Для кожної PAPER угоди створювати position record:

```text
position_id
decision_id
market_slug
event_url
selected_side
token_id
entry_timestamp_utc
entry_price
entry_best_ask
entry_best_bid
entry_spread
entry_size_usd
shares
fair_price_at_entry
max_buy_price_at_entry
edge_at_entry
round_open_price
btc_price_at_entry
distance_bucket_at_entry
volatility_bucket_at_entry
pattern_at_entry
stage_at_entry
seconds_to_expiry_at_entry
status: OPEN / SETTLED / CANCELLED / ERROR
```

---

## 23. Mark-to-market snapshots

Поки position OPEN, періодично записувати mark-to-market snapshot.

Для 5m ринків: кожні 10–15 секунд.

Для 15m ринків: кожні 15–30 секунд.

Fields:

```text
position_id
timestamp_utc
best_bid
best_ask
mid_price
estimated_exit_value_bid = shares * best_bid
unrealized_pnl_bid = estimated_exit_value_bid - entry_size_usd
btc_price
distance_from_round_open
seconds_to_expiry
```

---

## 24. Settlement

Після завершення market-а бот має отримати результат з Polymarket API/metadata.

Primary:

```text
settlement_source = POLYMARKET_API
```

Fallback, якщо Polymarket resolution ще недоступний:

```text
UP якщо final BTC price > round_open
DOWN якщо final BTC price < round_open
settlement_source = BINANCE_FALLBACK
```

Fallback settlement має бути позначений як non-authoritative.

Settlement fields:

```text
settlement_id
position_id
market_slug
resolved_outcome
selected_side
won
entry_price
shares
cost_usd
payout_usd = shares * 1.0 if won else 0.0
realized_pnl_usd = payout_usd - cost_usd
realized_roi_pct
settlement_source
round_open_price
round_close_price
final_btc_price
resolved_at_utc
```

---

## 25. Trade quality classification

Після settlement додати `trade_quality`.

```text
GOOD_WIN:
  won=true and edge_at_entry > 0 and no filter violation

BAD_WIN:
  won=true but soft warning existed, e.g. spread close to max or stale warning

GOOD_LOSS:
  won=false but decision was statistically valid and all filters passed

BAD_LOSS:
  won=false and entry violated filters, stale data, wrong rule, bad spread, bad timestamp alignment

EXECUTION_ERROR:
  missing orderbook, stale price, wrong token, wrong market, bad timestamp alignment, settlement mismatch
```

---

## 26. Storage

На першому етапі достатньо SQLite.

Tables:

```text
markets
market_snapshots
decisions
paper_orders
paper_positions
mark_to_market_snapshots
settlements
bot_runs
```

Кожен TRADE і кожен SKIP має бути збережений.

---

## 27. CLI runner

Continuous paper mode:

```bash
python scripts/run_polymarket_round_paper.py \
  --timeframe 5m \
  --mode paper
```

Explicit URL:

```bash
python scripts/run_polymarket_round_paper.py \
  --event-url "https://polymarket.com/uk/event/btc-updown-5m-1780652400" \
  --mode paper
```

One-shot decision:

```bash
python scripts/run_polymarket_round_paper.py \
  --event-url "https://polymarket.com/uk/event/btc-updown-5m-1780652400" \
  --mode paper \
  --once
```

---

## 28. Paper report

Додати CLI:

```bash
python scripts/paper_report.py --since "2026-06-01"
```

Report має показувати:

```text
total_decisions
total_trades
total_skips
skip reasons distribution
settled_trades
open_trades
win_count
loss_count
win_rate
total_realized_pnl
average_realized_pnl
median_realized_pnl
average_entry_price
average_fair_price_at_entry
average_edge_at_entry
average_spread_at_entry
average_seconds_to_expiry_at_entry
pnl by stage
pnl by pattern
pnl by distance_bucket
pnl by volatility_bucket
pnl by rule_id
best_trade
worst_trade
```

---

## 29. Per-trade CSV export

Додати CLI:

```bash
python scripts/export_paper_trades.py \
  --format csv \
  --out paper_trades.csv
```

CSV має містити по кожній угоді:

```text
position_id
market_slug
event_url
entry_time
settlement_time
side
entry_price
shares
cost_usd
payout_usd
realized_pnl_usd
realized_roi_pct
won
trade_quality
fair_price_at_entry
max_buy_price_at_entry
edge_at_entry
spread_at_entry
stage
pattern
current_side
distance_bucket
volatility_bucket
historical_probability
samples
rule_id
rule_match_type
round_open_price
btc_price_at_entry
final_btc_price
seconds_to_expiry_at_entry
settlement_source
```

---

## 30. Inspect one paper trade

Додати CLI:

```bash
python scripts/inspect_paper_trade.py --position-id POSITION_ID
```

Output має пояснити:

```text
1. Чому бот увійшов.
2. Який rule спрацював.
3. Який був historical_probability.
4. Який був ask.
5. Який був max_buy_price.
6. Який був edge.
7. Який був spread.
8. Який був BTC state.
9. Який був distance bucket.
10. Як змінювалась mark-to-market ціна.
11. Який був resolution.
12. PnL.
13. Класифікація: good decision / bad execution / bad signal / stale data / market moved against us.
```

---

## 31. Paper PnL formula

Paper має рахувати саме економіку угоди.

```text
Купили UP по 0.68 на $5
shares = 5 / 0.68 = 7.3529
cost = 5.00
```

Якщо UP виграв:

```text
payout = 7.3529
pnl = +2.3529
```

Якщо UP програв:

```text
payout = 0
pnl = -5.00
```

Formula:

```text
if won:
  payout_usd = shares * 1.0
else:
  payout_usd = 0.0

realized_pnl_usd = payout_usd - cost_usd
realized_roi_pct = realized_pnl_usd / cost_usd
```

---

## 32. Tests

Обов’язкові unit tests.

### test_url_parser.py

```text
- parses /uk/event/btc-updown-5m-1780652400
- parses /event/btc-updown-5m-1780652400
- parses slug only
- rejects invalid slug
- rejects unsupported timeframe
- extracts timeframe
- extracts timestamp
```

### test_round_state.py

```text
- computes ABOVE_OPEN
- computes BELOW_OPEN
- computes AT_OPEN
- assigns distance buckets correctly
- computes volatility bucket without leakage
- builds AFTER_5M state
- builds AFTER_10M state
```

### test_probability_rules.py

```text
- exact rule match
- fallback match
- no-trade if no rule
- no-trade if samples below threshold
- no-trade if historical_probability below threshold
- no-trade if return_aligned false
```

### test_signal_engine.py

```text
- TRADE if ask <= max_buy_price
- SKIP if ask > max_buy_price
- SKIP if spread too wide
- SKIP if liquidity too low
- SKIP if data stale
- SKIP if market inactive
```

### test_risk_manager.py

```text
- rejects if max open positions reached
- rejects if daily loss exceeded
- rejects duplicate position on same market
- allows valid risk
```

### test_paper_broker.py

```text
- creates paper order at best ask
- computes shares correctly
- prevents partial fill in v1
- prevents duplicate position
- tracks open position
```

### test_settlement.py

```text
- settles winning UP
- settles losing UP
- settles winning DOWN
- settles losing DOWN
- computes payout
- computes pnl
- marks settlement source
```

### test_reporting.py

```text
- paper report aggregates trades
- CSV export contains required fields
- inspect trade output includes decision reasoning
```

---

## 33. Paper run requirement

Після реалізації агент має обов’язково прогнати PAPER mode.

### Step 1: unit tests

```bash
pytest
```

### Step 2: one-shot explicit URL

```bash
python scripts/run_polymarket_round_paper.py \
  --event-url "https://polymarket.com/uk/event/btc-updown-5m-1780652400" \
  --mode paper \
  --once
```

Перевірити:

```text
URL parser works
market discovery works
UP/DOWN token IDs resolved
orderbook loaded
Binance state loaded
signal engine returned TRADE or SKIP
decision persisted
```

### Step 3: continuous paper mode

```bash
python scripts/run_polymarket_round_paper.py \
  --timeframe 5m \
  --mode paper
```

Прогнати кілька ринків/round-ів.

### Step 4: report

```bash
python scripts/paper_report.py --since "2026-06-01"
```

### Step 5: export

```bash
python scripts/export_paper_trades.py \
  --format csv \
  --out paper_trades.csv
```

### Step 6: inspect one trade

```bash
python scripts/inspect_paper_trade.py --position-id POSITION_ID
```

---

## 34. Acceptance criteria

Implementation accepted only if:

1. Bot runs in PAPER mode only.
2. No live trading code path is enabled in v1.
3. Explicit URL works: `https://polymarket.com/uk/event/btc-updown-5m-1780652400`.
4. Slug parser has tests.
5. Market discovery returns UP/DOWN token IDs.
6. Bot validates timestamp against Polymarket metadata.
7. Bot gets real orderbook best bid/ask.
8. Bot uses best ask for paper execution.
9. Bot uses state-bucket rules, not generic next-candle prediction.
10. Bot never opens paper trade if ask > max_buy_price.
11. Bot persists every TRADE and SKIP decision.
12. Bot persists full market/orderbook/BTC/signal/risk snapshot.
13. Bot creates paper position records.
14. Bot records mark-to-market snapshots.
15. Bot settles paper positions.
16. Bot calculates realized PnL.
17. Bot classifies trade quality.
18. Bot generates paper report.
19. Bot exports per-trade CSV.
20. Bot can inspect one trade by position id.
21. Unit tests pass.
22. README/run instructions are provided.
23. Final implementation report includes examples of TRADE, SKIP, settled position, report and CSV row.

---

## 35. Final deliverables from agent

Agent must provide:

1. Code implementation.
2. Config example.
3. SQLite schema/migration.
4. State rules JSON or loader.
5. Tests.
6. Paper run command.
7. Example one-shot output.
8. Example continuous paper output.
9. Paper report output.
10. CSV export example.
11. Inspect trade example.
12. Short implementation report.

---

## 36. Implementation note

The first version should prioritize correctness, traceability and auditability over profitability.

The bot is accepted only if every paper trade can be reconstructed later:

```text
why entry happened
what market price was
what fair price was
what max buy price was
what BTC state was
what rule matched
what risk checks passed
how position moved before expiry
what resolution was
whether trade won or lost
what realized PnL was
```
