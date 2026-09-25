import os
import re
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from metaapi_cloud_sdk import MetaApi

TELEGRAM_TOKEN = os.getenv("TOKEN")
METAAPI_TOKEN = os.getenv("API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

async def place_trade(symbol, direction, order_type, entry, sl, tps, volume=0.01):
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()
        await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    # Expiration = 24 hours from now
    expiration = datetime.utcnow() + timedelta(hours=24)

    results = []
    volume_per_tp = round(volume / len(tps), 2)
    if volume_per_tp < 0.01:
        volume_per_tp = 0.01

    try:
        for i, tp in enumerate(tps):
            current_volume = volume_per_tp if i < len(tps) - 1 else round(volume - volume_per_tp * (len(tps) - 1), 2)

            options = {
                "stopLoss": float(sl),
                "takeProfit": float(tp)
            }

            if order_type != "MARKET":
                options["expiration"] = {
                    "type": "ORDER_TIME_SPECIFIED",
                    "time": expiration.isoformat() + "Z"
                }

            if order_type == "MARKET":
                if direction == "BUY":
                    result = await connection.create_market_buy_order(symbol, current_volume, **options)
                else:
                    result = await connection.create_market_sell_order(symbol, current_volume, **options)

            elif order_type == "LIMIT":
                if direction == "BUY":
                    result = await connection.create_limit_buy_order(symbol, current_volume, float(entry), **options)
                else:
                    result = await connection.create_limit_sell_order(symbol, current_volume, float(entry), **options)

            elif order_type == "STOP":
                if direction == "BUY":
                    result = await connection.create_stop_buy_order(symbol, current_volume, float(entry), **options)
                else:
                    result = await connection.create_stop_sell_order(symbol, current_volume, float(entry), **options)

            results.append(f"TP{i+1}: {tp}")

        return f"✅ Order(s) placed!\n\n{direction} {order_type} {symbol}\nEntry: {entry or 'Market'}\nSL: {sl}\n" + "\n".join(results)
    except Exception as e:
        return f"❌ Error: {str(e)}"
    finally:
        await connection.close()

def parse_signal(text):
    text = text.upper().replace(",", ".")

    # Replace GOLD with XAUUSD
    text = text.replace("GOLD", "XAUUSD")

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

    # Entry price
    entry = None
    entry_match = re.search(r'(?:AT|@|ENTRY|PRICE)[:\s]*([\d.]+)', text)
    if entry_match:
        entry = entry_match.group(1)
    else:
        price_match = re.search(r'(?:BUY|SELL)\s+(?:LIMIT|STOP)\s+(?:AT\s+)?([\d.]+)', text)
        if price_match:
            entry = price_match.group(1)

    # SL
    sl = None
    sl_match = re.search(r'(?:SL|STOP\s*LOSS|S/L)[:\s]*([\d.]+)', text)
    if sl_match:
        sl = sl_match.group(1)

    # Multiple TPs
    tps = re.findall(r'(?:TP|TAKE\s*PROFIT|T/P)[:\s]*([\d.]+)', text)
    if not tps:
        # Also catch lines that just say TP1 4285, TP2 4300 etc.
        tps = re.findall(r'TP\d*[:\s]*([\d.]+)', text)

    if direction and symbol and sl and tps:
        if order_type != "MARKET" and not entry:
            return None
        return symbol, direction, order_type, entry, sl, tps
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    parsed = parse_signal(text)

    if not parsed:
        await update.message.reply_text(
            "❌ Could not understand the signal.\n\n"
            "Need: BUY/SELL + Symbol (or GOLD) + SL + at least one TP"
        )
        return

    symbol, direction, order_type, entry, sl, tps = parsed

    await update.message.reply_text(
        f"Processing {direction} {order_type} {symbol}...\n"
        f"Entry: {entry or 'Market'} | SL: {sl} | TPs: {', '.join(tps)}"
    )

    result = await place_trade(symbol, direction, order_type, entry, sl, tps)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
