# ===== Настройки kalashidzee trade signal bot (версия 2: 1ч + вход на ретесте) =====
# Только ликвидные монеты с фундаменталом. Мемкоинов тут нет специально.
SYMBOLS = ["BTC", "ETH", "SOL", "HYPE", "BNB", "XRP", "LINK", "AVAX"]


def inst(sym):
    return f"{sym}-USDT-SWAP"   # бессрочный контракт OKX, публичные данные, ключи не нужны


MIN_VOLUME_24H_USDT = 50_000_000   # фильтр ликвидности (суточный оборот в USDT)

BAR = "1H"          # таймфрейм поиска пробоя
TRACK_BAR = "15m"   # по каким свечам следим за лимиткой и сделкой

# Параметры, выбранные оптимизатором (1ч, флэт до 5%, объём x1.5, стоп 0.5 ATR, цели 2-3-5, тренд, вход на ретесте)
STRATEGY = dict(
    range_bars=24,          # окно флэта: 24 часовые свечи = 24 часа
    max_range_pct=5.0,
    break_buffer_pct=0.10,
    vol_mult=1.5,
    vol_len=20,
    close_strength=0.65,
    atr_len=14,
    stop_atr=0.5,
    min_stop_pct=0.3,
    max_stop_pct=4.0,
    use_trend=True,
    ema_len=100,
    tp_r=(2.0, 3.0, 5.0),
    tp_split=(0.5, 0.25, 0.25),
)

RETEST_BUFFER_PCT = 0.05   # лимитка ставится на 0.05% от уровня в сторону пробоя
RETEST_WAIT_BARS = 8       # лимитка живёт 8 часов
MAX_HOLD_HOURS = 48        # если сделка висит дольше, закрываем по рынку
FEE_PCT = 0.06
