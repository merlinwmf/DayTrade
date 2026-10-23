from futu import OpenQuoteContext, SubType, RET_OK

STOCK = 'US.AAPL'

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

ret, err = quote_ctx.subscribe([STOCK], [SubType.QUOTE], subscribe_push=False)
if ret != RET_OK:
    print('Subscribe failed:', err)
    quote_ctx.close()
    raise SystemExit(1)

ret, data = quote_ctx.get_stock_quote([STOCK])
if ret == RET_OK:
    print(data)
else:
    print('get_stock_quote failed:', data)

quote_ctx.close()
