You are my ICT Institutional Strategy Engineer and Quant Trading System Architect.

Your task is NOT to explain ICT.

Your task is to build professional ICT trading strategies using institutional logic including:

Market Structure
Market Flow
Liquidity
Liquidity Sweeps
Liquidity Pools
Liquidity Voids
MSS (Market Structure Shift)
BOS
Order Blocks
Breaker Blocks
Mitigation Blocks
Fair Value Gap (FVG)
Inversion FVG
Premium / Discount
OTE
Dealing Range
Power of Three / AMD
Kill Zones
Session Logic
SMT Divergence
Judas Swing
ICT Buy/Sell Models
Time & Price Theory
Institutional Order Flow
Risk Management
Higher Timeframe Alignment
Top Down Analysis
STRATEGY CREATION RULES

Do NOT create generic strategies.

Every strategy must be:

independent
rule-based
measurable
professional
institutional
executable

Avoid vague rules.

BAD:

"enter on strong move"

GOOD:

"enter after liquidity sweep + MSS + displacement candle leaving FVG inside premium/discount zone"

No social-media ICT.

No random setups.

No discretionary-only logic.

If setup is discretionary:

explain interpretation
convert to measurable conditions
define filters

Only produce trades when complete model exists.

Otherwise:

NO TRADE.

MARKETS

Create separate strategy systems for:

XAUUSD
NASDAQ
US30
EURUSD
GBPUSD

For EACH market create:

A. Beginner Strategy
B. Intermediate Strategy
C. Advanced Strategy

Total:

15 professional strategies.

Each must be unique.

Do NOT clone logic across markets.

Adapt to:

volatility
liquidity profile
session behavior
institutional movement
instrument characteristics
REQUIRED STRATEGY STRUCTURE

For EACH strategy create:

1 Market Environment

Define:

trending
ranging
reversal
accumulation
distribution

Explain when strategy works.

Explain when it fails.

2 Higher Timeframe Bias Engine

Bias must use:

Weekly structure
Daily structure
H4 flow
Liquidity objective
Premium/Discount
External/Internal liquidity
Market flow alignment

Bias outputs:

Bullish
Bearish
Neutral
No Trade

No guessing.

3 Liquidity Logic

Identify:

buy-side liquidity
sell-side liquidity
equal highs/lows
engineered liquidity
stop pools
inducement
sweep conditions

Trade only if clear liquidity objective exists.

No liquidity target = No trade.

4 MSS / BOS Framework

Define:

Bullish:

liquidity sweep
displacement
bullish MSS
imbalance creation

Bearish:

liquidity sweep
bearish MSS
displacement
imbalance

Explain valid vs fake MSS.

5 Entry Framework

Build entry sequence.

Preferred sequence:

Liquidity Sweep
→ MSS
→ Displacement
→ FVG / OB / Breaker
→ Confirmation
→ Entry

Explain:

exact trigger
candle logic
confirmation hierarchy

No vague entries.

6 PD Array Logic

Use:

FVG
IFVG
OB
Breaker
Mitigation
Liquidity Void
Premium/Discount

Explain:

priority
confluence
invalidation

Rank strongest arrays.

7 OTE Logic

Use:

dealing range
Fibonacci OTE
premium/discount

Only allow OTE if:

HTF aligned
liquidity objective exists
structure confirms

No blind OTE entries.

8 Session Model

Use ICT timing logic.

Include:

Asian
London Open
London Close
New York Open
Kill Zones

Explain:

ideal sessions
avoid sessions
timing windows
volatility expectations

No 24/7 trading logic.

9 Judas Swing + AMD Logic

Explain use of:

Accumulation
Manipulation
Distribution

Include:

fake move
liquidity raid
reversal logic
institutional expansion

Where applicable.

10 SMT Logic

Where relevant:

Use SMT divergence.

Explain:

confirmation
invalid SMT
correlation logic

Optional filter.

Not mandatory.

11 Stop Loss Logic

Use:

structural invalidation
liquidity invalidation
swing logic

Explain:

why SL belongs there
when setup invalidates

Avoid arbitrary stops.

12 Take Profit Logic

Targets must use:

liquidity objective
opposing liquidity
external range
session expansion
PD arrays

Minimum RR:

2R

Preferred:

3R+

Explain TP hierarchy.

13 Trade Management

Include:

BE rules
partial exits
scaling
trailing
hold conditions
exit rules

Rule-based only.

14 Invalid Conditions

Force NO TRADE when:

weak displacement
poor structure
no liquidity
session mismatch
news instability
fake MSS
poor RR
chop
HTF conflict

No forced trades.

HIGH PROBABILITY FILTER

Strategy is valid ONLY if:

✓ HTF bias aligned
✓ liquidity objective exists
✓ MSS confirmed
✓ displacement exists
✓ FVG or PD array present
✓ session aligned
✓ RR valid

Else:

NO TRADE.

FINAL OUTPUT

For each market provide:

1 Beginner Strategy
1 Intermediate Strategy
1 Advanced Strategy

Then create:

MASTER ICT STRATEGY

Combine strongest institutional logic into one:

High Probability Institutional ICT Model

using:

liquidity
MSS
FVG
OB
premium/discount
session timing
risk management

Deliver professional markdown.

Production-quality only.

Generate plan run so I can tell claude code ai to start writing the code one by one line . 
For each concept create seperate generic file such that we can try multiple possibility

add whole plan in ICT-dry-run-plan.md file 
in small small phase