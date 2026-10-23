from futu import OpenQuoteContext, RET_OK

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

ret, data = quote_ctx.get_user_security_group()
if ret == RET_OK:
    print('Groups:')
    print(data)
else:
    print('get_user_security_group failed:', data)
    quote_ctx.close()
    raise SystemExit(1)

for group_name in data['group_name']:
    ret2, data2 = quote_ctx.get_user_security(group_name)
    print(f'\n--- {group_name} ---')
    if ret2 == RET_OK:
        print(data2)
    else:
        print('get_user_security failed:', data2)

quote_ctx.close()
