import os
import re
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from metaapi_cloud_sdk import MetaApi

TELEGRAM_TOKEN = os.getenv("TOKEN")
METAAPI_TOKEN = os.getenv("API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

async def place_trade(symbol, direction, order_type, entry, sl, tp, volume=0.01):
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()
        await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    try:
        if order_type == "MARKET":
            if direction == "BUY":
                result = await connection.create_market_buy_order(symbol, volume, stop_loss=float(sl), take_profit=float(tp))
            else:
                result = await connection.create_market_sell_order(symbol, volume, stop_loss=float(sl), take_profit=float(tp))

        elif order_type == "LIMIT":
            if direction == "BUY":
                result = await connection.create_limit_buy_order(symbol, volume, float(entry), stop_loss=float(sl), take_profit=float(tp))
            else:
                result = await connection.create_limit_sell_order(symbol, volume, float(entry), stop_loss=float(sl), take_profit=float(tp))

        elif order_type == "STOP":
            if direction == "BUY":
                result = await connection.create_stop_buy_order(symbol, volume, float(entry), stop_loss=float(sl), take_profit=float(tp))
            else:
                result = await connection.create_stop_sell_order(symbol, volume, float(entry), stop_loss=float(sl), take_profit=float(tp))

        return f"✅ Order placed!\n\n{direction} {order_type} {symbol}\nEntry: {entry or 'Market'}\nSL: {sl}\nTP: {tp}"
    except Exception as e:
        return f"❌ Error: {str(e)}"
    finally:
        await connection.close()

def parse_signal(text):
    text = text.upper().replace(",", ".")

    # Direction
    direction = None
    if re.search(r'\bBUY\b', text):
        direction = "BUY"
    elif re.search(r'\bSELL\b', text):
        direction = "SELL"

    # Order type
    order_type = "MARKET"
    if "LIMIT" in text:
        order_type = "LIMIT"
    elif "STOP" in text:
        order_type = "STOP"

    # Symbol
    symbols = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "BTCUSD", "ETHUSD"]
    symbol = None
    for s in symbols:
        if s in text:
            symbol = s
            break

    # Entry price (for pending orders)
    entry = None
    entry_match = re.search(r'(?:AT|@|ENTRY|PRICE)[:\s]*([\d.]+)', text)
    if entry_match:
        entry = entry_match.group(1)
    else:
        # Sometimes the price is right after BUY/SELL LIMIT
        price_match = re.search(r'(?:BUY|SELL)\s+(?:LIMIT|STOP)\s+(?:AT\s+)?([\d.]+)', text)
        if price_match:
            entry = price_match.group(1)

    # SL
    sl = None
    sl_match = re.search(r'(?:SL|STOP\s*LOSS|S/L)[:\s]*([\d.]+)', text)
    if sl_match:
        sl = sl_match.group(1)

    # TP
    tp = None
    tp_match = re.search(r'(?:TP|TAKE\s*PROFIT|T/P)[:\s]*([\d.]+)', text)
    if tp_match:
        tp = tp_match.group(1)

    if direction and symbol and sl and tp:
        if order_type != "MARKET" and not entry:
            return None  # Pending orders need an entry price
        return symbol, direction, order_type, entry, sl, tp
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    parsed = parse_signal(text)

    if not parsed:
        await update.message.reply_text(
            "❌ Could not understand the signal.\n\n"
            "Need: BUY/SELL + Symbol + SL + TP\n"
            "For Limit/Stop orders also need the entry price."
        )
        return

    symbol, direction, order_type, entry, sl, tp = parsed

    await update.message.reply_text(
        f"Processing {direction} {order_type} {symbol}...\n"
        f"Entry: {entry or 'Market'} | SL: {sl} | TP: {tp}"
    )

    result = await place_trade(symbol, direction, order_type, entry, sl, tp)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
