from futu import (
    OpenQuoteContext, RET_OK,
    SetPriceReminderOp, PriceReminderType, PriceReminderFreq, PriceReminderMarketStatus,
)

STOCK = 'US.INTC'

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

ret, err = quote_ctx.subscribe([STOCK], ['QUOTE'], subscribe_push=False)
if ret != RET_OK:
    print('Subscribe failed:', err)
    quote_ctx.close()
    raise SystemExit(1)

ret, data = quote_ctx.get_stock_quote([STOCK])
if ret != RET_OK:
    print('get_stock_quote failed:', data)
    quote_ctx.close()
    raise SystemExit(1)

last_price = float(data.loc[0, 'last_price'])
print(f'{STOCK} current last_price = {last_price}')

trigger_price = round(last_price - 0.01, 2)

ret, res = quote_ctx.set_price_reminder(
    code=STOCK,
    op=SetPriceReminderOp.ADD,
    reminder_type=PriceReminderType.PRICE_UP,
    reminder_freq=PriceReminderFreq.ONCE,
    value=trigger_price,
    note='TEST ALERT - Claude Code',
    reminder_session_list=[
        PriceReminderMarketStatus.OPEN,
        PriceReminderMarketStatus.US_PRE,
        PriceReminderMarketStatus.US_AFTER,
        PriceReminderMarketStatus.US_OVERNIGHT,
    ],
)

if ret == RET_OK:
    print('Price reminder created OK, key =', res)
else:
    print('set_price_reminder failed:', res)

quote_ctx.close()
